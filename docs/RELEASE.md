# Release Checklist

## Offline gates

1. Run every plugin Python test:

   ```powershell
   Get-ChildItem -LiteralPath .\plugins\picturebook-screenwriter -Recurse -Filter test_*.py |
     ForEach-Object { python $_.FullName; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
   ```

2. Run the image-generate Node test:

   ```powershell
   node --test .\plugins\picturebook-screenwriter\skills\image-generate\generate.test.js
   ```

3. Run the staging regression:

   ```powershell
   node .\plugins\picturebook-screenwriter\skills\staging-planner\scripts\run_regression.js
   ```

4. Run plugin structure validation:

   ```powershell
   python C:\Users\lvan\.codex\skills\.system\plugin-creator\scripts\validate_plugin.py .\plugins\picturebook-screenwriter
   ```

5. Run governance and package checks:

   ```powershell
   python .\scripts\governance_check.py
   python .\scripts\package_check.py
   ```

6. Confirm the version in `plugins/picturebook-screenwriter/.codex-plugin/plugin.json`, `docs/CHANGELOG.md`, and the release tag describe the same release. Run the release-version gate with the tag value:

   ```powershell
   $env:PICTUREBOOK_EXPECTED_VERSION = "<release-version>"
   python .\plugins\picturebook-screenwriter\tests\test_release_contract.py
   Remove-Item Env:PICTUREBOOK_EXPECTED_VERSION
   ```

7. Confirm no credentials, run directories, or `.picturebook-screenwriter` files are committed.

## Explicit live gates

1. Run the documented multi-user live Feishu acceptance only after both users approve.
2. Run live image generation only with explicit user confirmation, an explicit output directory, explicit size and quality, and a supplied API key.
3. Confirm no key appears in command history, logs, metadata, or exports.
4. Install from the published Git ref in a clean Codex profile.
5. Record acceptance results and blockers before publishing to the marketplace.
