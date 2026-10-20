# Third-party notices

Altium Agent is Apache-2.0 (LICENSE). These parts come from MIT-licensed
projects and stay under the MIT licence; each file names its origin in its
header. docs/PROVENANCE.md says what was taken and what changed.

## altium-mcp-pro

`src/altium_agent/offline/files/` (from altium-mcp-pro `formats/`),
`src/altium_agent/offline/errors.py`, the `find_projects` walk in
`src/altium_agent/offline/context.py`, `src/altium_agent/analysis/`
`model.py`, `bom.py`, `consistency.py`, `copper.py`, `dfm.py`, `erc.py`,
`overlap.py`, `review.py`, `si.py`, `board.py`, the tool modules in
`src/altium_agent/tools/offline/` (except `publish.py` and `__init__.py`),
and `tests/offline_files/` (except `conftest.py` and
`test_altium_agent_offline.py`).

```
MIT License

Copyright (c) 2026 altium-mcp-pro contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## kicad-mcp-pro

`src/altium_agent/analysis/engines/` and its `data/` folder came through
altium-mcp-pro, which adapted them from kicad-mcp-pro.

```
MIT License

Copyright (c) 2026 Osman Aslan

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
