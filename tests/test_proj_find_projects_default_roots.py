# SPDX-License-Identifier: Apache-2.0
"""proj_find_projects without a root searches Documents (it used to raise)."""

from altium_agent.tools.offline.project import project_find


def test_no_root_searches_documents(tmp_path, monkeypatch):
    docs = tmp_path / "Documents" / "Boards" / "A"
    docs.mkdir(parents=True)
    (docs / "A.PrjPcb").write_text("[Design]\n")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("PUBLIC", raising=False)
    monkeypatch.delenv("ALTIUM_AGENT_PROJECT", raising=False)
    out = project_find()
    assert str(tmp_path / "Documents") in out["searched"]
    assert any(p["name"].startswith("A") for p in out["projects"])
