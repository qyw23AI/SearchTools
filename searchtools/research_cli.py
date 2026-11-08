"""JSON stdin adapter for installed skills before MCP tools are reloaded."""
import json
import sys
from .mcp_server import research_call

if __name__ == '__main__':
    request = json.load(sys.stdin)
    print(research_call(request['operation'], request.get('arguments', {})))
