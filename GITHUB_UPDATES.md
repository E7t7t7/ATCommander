# Updating Commander

The **Update Commander** button downloads app updates from
[E7t7t7/ATCommander](https://github.com/E7t7t7/ATCommander), branch `main`.
It is separate from **Check / update game client**, which checks compatibility
with the game's current public release.

Click **Update Commander**. If an update is ready, finish or stop your active
work, close Commander normally, then reopen **AT Commander.exe**. Installation
happens before the interface opens. No Git installation or GitHub login is
needed. There is no automatic game action, forced restart, or background poll.
**Stop all**, Cancel download and closing stop an unfinished download.

The updater pins the manifest and archive to the same GitHub commit, validates
every managed file's SHA-256 and Python syntax, and rejects unsafe archive paths.
Only public source, documentation, requirements and supporting launcher scripts
can be managed. Your `.atcommander` account data, credentials, settings, queues,
browser profile, local environment and logs are outside that allowlist.
Existing files are backed up under `.commander-updates` before replacement. A
failed install rolls back; an interrupted install recovers on the next launch.
A second Commander instance cannot install over an open instance.

The existing native EXE is a stable starter and is not replaced by source
updates. If a future release changes that EXE itself, distribute its full ZIP.
Changed Python dependencies are installed by the normal EXE setup flow after
the source update. Always reopen the EXE rather than running app.py directly
when requirements change. Old unlisted files are retained, not deleted.
Backups remain local; they are excluded from Git and release ZIPs.

## Publishing an update

1. Change and test the public source in a clean checkout. Do not copy runtime
   files from an account's live installation into the checkout.
2. Run `python tools/make_update_manifest.py`. This updates
   `commander-update.json` using the public-file allowlist. The version defaults
   to 1.7.26; use `--version X.Y.Z` for a later named release.
3. Commit the changed public files and manifest together, then push `main`.

The updater checks actual file hashes, so fixes can be distributed even when
the display version stays the same. GitHub's `main` branch is the trusted
publication source; its changes become executable application code only after
the user requests an update and reopens Commander. Hashes check package integrity,
not independent publisher signatures. A missing, stale or malformed manifest
keeps the installed version intact. GitHub access errors and rate limits show a
retry message without logging request data.
