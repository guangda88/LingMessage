#!/bin/bash
cd /home/ai/lingmessage || exit 1
exec /usr/bin/python3 -m mcp_servers.lingbus_server "$@"
