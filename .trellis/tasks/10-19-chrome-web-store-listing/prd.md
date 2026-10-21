# Chrome Web Store listing preparation

## Goal

Publish the StashSeek Chat extension (0.2.0+, side panel) on the Chrome Web
Store, unlisted first for internal testers, with disclosures that match what
the code actually does.

## Facts

- **Registration.** Developer registration costs a one-time fee. Third-party
  sources say US$5; the official page states no amount. The account needs
  2-Step Verification and a trader/non-trader declaration (EEA). New accounts
  get 2 extension slots by default.
- **Privacy practices tab.** These parts are required:
  - single purpose;
  - a justification for every permission;
  - a remote-code declaration (we use none);
  - data-use categories plus Limited Use certification;
  - a privacy policy URL whose content must match the disclosures.
- **Extension ID change.** The store assigns its own item ID, so the current
  self-generated manifest `key` (ID `omogodipchfidpikpeebgmlplpkjnpfm`) will
  not match. After the first draft upload:
  1. Copy the dashboard public key into `manifest.json`.
  2. Add `chrome-extension://<store-id>` to `BROWSER_COMPANION_ALLOWED_ORIGINS`.
     This is set in `deploy/systemd/notebook-agent.service`, so it is a code
     change plus a deploy.
  3. Keep the old ID during the transition, so zip installs keep working.
- **Upload target.** Upload only the production build, because one manifest
  carries exactly one API origin.

## Child tasks

| Child | Deliverable |
| --- | --- |
| `10-19-privacy-policy-page` | Public privacy policy at the production origin, matching the data the code handles |
| (later) store listing kit | Single-purpose text, permission justifications, data-category answers, listing copy and screenshots |
| (later) store ID switch | Manifest `key` and `BROWSER_COMPANION_ALLOWED_ORIGINS` for the store ID, keeping the zip ID during the transition |
| (later, optional) permission trim | Drop NTULearn/Kaltura host permissions from the store build if NTU support is not offered publicly |

## Out of scope

Paid features, trader verification paperwork, and Firefox/Edge stores.
