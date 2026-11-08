#!/bin/bash
pkill -f '^/usr/bin/sunshine' 2>/dev/null
sleep 1
for i in {1..30}; do
    XAUTH=$(ls -t /tmp/xauth_* 2>/dev/null | head -1)
    if [ -n "$XAUTH" ]; then
        exec env DISPLAY=:0 XAUTHORITY="$XAUTH" /usr/bin/sunshine
    fi
    sleep 1
done
echo "Failed to find xauth file" >&2
exit 1
