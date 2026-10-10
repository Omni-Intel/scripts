# Moving remote contents

`move-remote.sh` requires Bash and rclone. It previews changes by default;
append `--execute` to perform the move. Run the examples from the `utils` directory.

```bash
# Move contents to another location; existing destination contents may be merged.
bash move-remote.sh 'omni-tos:bucket' 'omni-tos:bucket2/demo'

# Perform the move after reviewing the preview.
bash move-remote.sh 'omni-tos:bucket' 'omni-tos:bucket2/demo' --execute
```

Moves reject overlapping source/destination paths and stop if the source cannot
be listed. Remote paths containing `.` or `..`
segments are rejected. Directory moves remove empty source directories.
