#!/bin/bash
cd /root/Scripturi
export RICHPANEL_MCP_TOKEN=$(grep "^RICHPANEL_MCP_TOKEN=" .env | cut -d= -f2-)
export RICHPANEL_DB=/root/Scripturi/data/richpanel_tickets.db
exec .venv/bin/python3 -u team-intelligence/plugins/gigi/skills/richpanel-export/richpanel_apply.py "$@"
