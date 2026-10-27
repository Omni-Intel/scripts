#!/usr/bin/env bash
set -Eeuo pipefail

mode="${1:---dry-run}"
case "$mode" in
  --dry-run|--apply) ;;
  *) echo "用法: sudo bash $0 [--dry-run|--apply]" >&2; exit 2 ;;
esac

if (( EUID != 0 )); then
  echo "请使用 sudo 或 root 运行。" >&2
  exit 1
fi

sshd_bin="/nix/var/ml-platform-env/bin/sshd"
config="/mlplatform/sshd_config"
threshold=$((20 * 1024 * 1024 * 1024))

[[ -x "$sshd_bin" && -f "$config" ]] || {
  echo "SSH 程序或配置文件不存在，请重新检查平台配置。" >&2
  exit 1
}

declare -a denied=()

printf '%-20s %12s  %s\n' "用户" "占用(GiB)" "结果"

while IFS=: read -r username _ uid _ _ home shell; do
  (( uid >= 1000 && uid < 65534 )) || continue
  [[ "$home" == "/home/$username" ]] || continue

  # 限制用户名格式，确保可以安全写入 sshd 配置。
  [[ "$username" =~ ^[a-z_][a-z0-9_-]*[$]?$ ]] || {
    echo "用户名格式不受支持：$username" >&2
    exit 1
  }

  [[ -d "$home" ]] || {
    echo "主目录不存在：$home；停止，未修改配置。" >&2
    exit 1
  }

  if ! usage_line=$(du -s --block-size=1 -- "$home"); then
    echo "统计 $home 失败；停止，未修改配置。" >&2
    exit 1
  fi
  usage="${usage_line%%[[:space:]]*}"

  if (( usage > threshold )); then
    denied+=("$username")
    result="拒绝新 SSH 连接"
  else
    result="不限制"
  fi

  awk -v user="$username" -v bytes="$usage" -v result="$result" \
    'BEGIN { printf "%-20s %12.2f  %s\n", user, bytes / 1073741824, result }'
done < <(getent passwd)

echo
echo "超过 20 GiB 的用户数：${#denied[@]}"

if [[ "$mode" == "--dry-run" ]]; then
  echo "预览完成，未修改配置。使用 --apply 应用。"
  exit 0
fi

# 只处理已确认的监听进程；找不到或找到多个时停止。
mapfile -t listener_pids < <(
  ps -eo pid=,args= |
    awk '$2 == "sshd:" &&
         $3 == "/nix/var/ml-platform-env/bin/sshd" &&
         $0 ~ /\[listener\]/ { print $1 }'
)

if (( ${#listener_pids[@]} != 1 )); then
  echo "无法唯一确定 SSH 监听进程；停止，未修改配置。" >&2
  exit 1
fi
listener_pid="${listener_pids[0]}"

tmp=$(mktemp "${config}.quota.XXXXXX")
backup=$(mktemp "${config}.backup.XXXXXX")
trap 'rm -f -- "$tmp" "$backup"' EXIT

cp -p -- "$config" "$backup"
cp -p -- "$config" "$tmp"

# 更新专用区块，保留配置中的其他内容。拒绝处理已有 Match
# 配置，避免将全局 DenyUsers 意外写进条件配置段。
python3 - "$tmp" "${denied[@]}" <<'PY'
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
users = sys.argv[2:]
text = path.read_text(encoding="utf-8")

begin = "# BEGIN home-quota SSH restrictions"
end = "# END home-quota SSH restrictions"

if text.count(begin) != text.count(end) or text.count(begin) > 1:
    raise SystemExit("管理区块标记不一致，请人工检查配置。")

if begin in text:
    pattern = re.compile(
        r"(?m)^" + re.escape(begin) + r"\s*\n.*?^" +
        re.escape(end) + r"[^\n]*(?:\n|$)",
        re.DOTALL,
    )
    text, count = pattern.subn("", text)
    if count != 1:
        raise SystemExit("无法安全解析管理区块，请人工检查配置。")

if re.search(r"(?im)^\s*Match\s+", text):
    raise SystemExit("配置中存在 Match 段，请人工调整脚本后再应用。")

block = begin + "\n"
if users:
    block += "DenyUsers " + " ".join(users) + "\n"
block += end + "\n"

path.write_text(text.rstrip("\n") + "\n\n" + block, encoding="utf-8")
PY

if ! "$sshd_bin" -t -f "$tmp"; then
  echo "新 SSH 配置验证失败，未修改原配置。" >&2
  exit 1
fi

effective=$("$sshd_bin" -T -f "$tmp")
for username in "${denied[@]}"; do
  if ! awk -v user="$username" '
    $1 == "denyusers" {
      for (i = 2; i <= NF; i++)
        if ($i == user) found = 1
    }
    END { exit !found }
  ' <<< "$effective"; then
    echo "有效配置中未找到 $username，未修改原配置。" >&2
    exit 1
  fi
done

# 再确认监听进程仍存在。
if ! kill -0 "$listener_pid" 2>/dev/null; then
  echo "SSH 监听进程已退出，未修改原配置。" >&2
  exit 1
fi

mv -f -- "$tmp" "$config"

if ! kill -HUP "$listener_pid"; then
  echo "发送重载信号失败，正在恢复原配置。" >&2
  cp -p -- "$backup" "$config"
  kill -HUP "$listener_pid" 2>/dev/null || true
  exit 1
fi

echo "配置已更新，已向 SSH 监听进程 $listener_pid 发送重载信号。"
