"""Stand-in for nfc-agent during development (no reader needed).

Serves the same GET /events and GET /health as agent.py, and adds
POST /inject?uid=04A1B2C3D4E5F6 to simulate a touch. Uses port 8100 like the
real agent, so stop the real one first.

  docker run --rm -d --name nfc-fake-agent -p 8100:8100 -v "$PWD/nfc-agent":/agent:ro \
      nfc-agent:latest python3 -u /agent/fake_agent.py
  curl -X POST "localhost:8100/inject?uid=04A1B2C3D4E5F6"
"""
import os
import sys
import types
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.modules.setdefault("nfc", types.ModuleType("nfc"))  # the HTTP side of agent.py does not need nfcpy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent  # noqa: E402

agent.state.update(reader=True, path="fake")


class Handler(agent.Handler):
    def do_POST(self):
        url = urlparse(self.path)
        uid = parse_qs(url.query).get("uid", [""])[0].strip()
        if url.path != "/inject" or not uid:
            return self._json(404, {"error": "use POST /inject?uid=..."})
        agent.publish(uid.upper())
        return self._json(200, {"ok": True, "uid": uid.upper()})


if __name__ == "__main__":
    agent.log.info("fake agent listening on :%d (POST /inject?uid=...)", agent.PORT)
    ThreadingHTTPServer(("0.0.0.0", agent.PORT), Handler).serve_forever()
