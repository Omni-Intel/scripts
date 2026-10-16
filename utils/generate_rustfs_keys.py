
#!/usr/bin/env python3

import argparse
import secrets
import string

ALPHABET = string.ascii_uppercase + string.digits


def generate_key(prefix: str, name: str, key_length) -> str:
    fixed = prefix + name.upper()

    if not fixed.isascii() or not fixed.isalnum():
        raise ValueError("Only ASCII letters and digits are allowed")

    if len(fixed) >= key_length:
        raise ValueError(
            "Prefix and name must be shorter than 32 characters"
        )

    random_length = key_length - len(fixed)

    suffix = "".join(
        secrets.choice(ALPHABET)
        for _ in range(random_length)
    )

    return fixed + suffix


def main():
    parser = argparse.ArgumentParser(
        description="Generate RustFS AK/SK credentials"
    )
    parser.add_argument(
        "--name",
        default="RUSTFS",
        help="Custom identifier (default: RUSTFS)"
    )
    args = parser.parse_args()

    name = args.name.upper()

    if not name.isascii() or not name.isalnum():
        parser.error(
            "Name must contain only ASCII letters and digits"
        )

    if len(name) > 20:
        parser.error(
            "Name must be at most 20 characters"
        )

    access_key = generate_key("AK", name, 16)
    secret_key = generate_key("SK", name, 32)

    print(f"RUSTFS_ACCESS_KEY={access_key}")
    print(f"RUSTFS_SECRET_KEY={secret_key}")


if __name__ == "__main__":
    main()
