"""
### Jinja2 templates for the admin landing app.

Templates live in `app/templates/` and are rendered via
`fastapi.templating.Jinja2Templates`. Routes hand off to
`templates.TemplateResponse(...)` with a request and context dict;
Jinja2 auto-escapes interpolated values (no manual `html.escape`
required) and gives every page the shared chrome via `{% extends
"base.html" %}`.

Layout:

- `base.html` — `<html>` shell, CSS, blocks `body` and `scripts`.
- `login.html` — extends base for the unauthenticated landing.
- `dashboard.html` — extends base for the post-login dashboard, with
  the user-menu avatar, the four tile sections, and the launch JS.
- `_tile.html` — partial for one notebook tile (rendered per tile
  in each dashboard section). Underscore prefix is convention for
  "not directly rendered, included only".

Context shape consumed by `dashboard.html`:

- `title` (str) — `<title>` chrome.
- `github_username` (str) — for the avatar URL and "Signed in as".
- `workflows`, `snapshots`, `workspace`, `tutorials` (list of tile
  dicts) — each dict has `slug`, `title`, `description`, `section` (plus
  `cpu`/`memory` for workspace tiles). The dashboard loops and includes
  `_tile.html` for each. `snapshots` tiles are frozen: a Run-only launch
  (no Edit/gear/trash); a Workspace tile's 📸 button freezes it into this
  section. Workflows and Snapshots tiles also carry a Copy-to-workspace
  button.

spec: [docs/architecture/app.md](../docs/architecture/app.md)
"""

from pathlib import Path

from fastapi.templating import Jinja2Templates

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
