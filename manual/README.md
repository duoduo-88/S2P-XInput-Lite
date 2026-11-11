# Manual sources

`USER_GUIDE.md` and `USER_GUIDE_zh-TW.md` are the current English and
Traditional Chinese manuals shipped in releases.

The guides cover desktop v0.8.0 and bundled S2P-FW 1.0.7.

`assets/v0.7.14/` contains English-interface screenshots shared by both guides.
The written procedures cover the current controls.

`build_manual.py` and `assets/raw/` preserve the source for the archived
v0.5.3 DOCX manual. They are not used by the current release package. The
script deliberately writes a filename containing `legacy-v0.5.3` so the
result cannot be mistaken for current documentation.

`rendered/` and `rendered-final/` are local visual-QA outputs and are ignored
by Git.
