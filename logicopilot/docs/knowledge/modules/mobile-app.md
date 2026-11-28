---
title: Cargora Support phone app
category: modules
module: mobile-app
owner: Senthil
status: ready
summary: The Expo (React Native) phone app for the support desk - what it shows, login, push alerts and how to build the APK.
---
# Cargora Support phone app

## What it does
The Production Support desk on a phone. A support user can:
- see Cargora's **health in one line** and today's counts,
- see the **problems that need action** (failed / delayed jobs) with the reason in plain words,
- open a job: why it failed, the ERP's corrections, timeline and notes,
- press **Retry ERP** / **Retry AI**, or **Mark handled**,
- add a **note with a status** (Investigating, Fixed, Customer informed, ERP side issue, Not our issue),
- read and write **Known issues** - the fix written once per cause, shown on every job with that cause,
- get **push notifications** for failed jobs, delays, stuck queues and anything DOWN.

It is separate from the Cargora **Manager** app (that one is for customer managers).

## Screens
| Screen | File |
|---|---|
| Login | `src/app/login.tsx` |
| Home (health, tiles, last alerts) | `src/app/(tabs)/index.tsx` |
| Needs action (open problems) | `src/app/(tabs)/issues.tsx` |
| Known issues | `src/app/(tabs)/known.tsx`, `src/app/known/[key].tsx` |
| One job | `src/app/job/[id].tsx` |

All paths are under `logicopilot/mobile/support-app`. Navigation is Expo Router (every file in `src/app` is a screen).

## How it works

### Login
1. The user signs in with a **support** login (the same account as the website). A Super Admin login is also accepted; any other role is refused with "This app is for the Production Support login only."
2. The app calls the normal login endpoint asking for tokens in the response (`token_response: true`) instead of cookies, because a phone app has no browser cookie jar.
3. The tokens are kept in the phone's **secure storage**. Every request sends them as a Bearer token.
4. The server defaults to production. "Server ... change" on the login screen switches to SIT for testing.

### Staying signed in (Remember me)
- The server session ends after a period of no activity, like the website.
- When a request is refused as signed out, the app first tries a token refresh once.
- If that fails and **Remember me** was ticked, the app signs in again by itself with the saved email and password. The password is stored **only** in the phone's secure storage (Android Keystore / iOS Keychain).
- If the password was changed on the website, the saved one is forgotten and the login screen is shown.
- Log out, or unticking Remember me, removes the saved password.

### The X-Requested-With header (must not be removed)
Every request from the app sends `X-Requested-With: XMLHttpRequest`. The backend refuses any
POST / PUT / DELETE without it (its CSRF protection) with the message **"Missing required header"**.
The website sends the same header. Any new request code in the app must keep it - it lives in
`BASE_HEADERS` in `src/lib/api.ts`.

### Push notifications
1. After login the app asks the phone for notification permission, creates the Android channel "Cargora alerts", and gets an **Expo push token**.
2. It registers the token with the server (`POST /support/app/devices`). Log out removes it (`/support/app/devices/remove`).
3. The server's alert loop (every 5 minutes, see the Production Support page) sends each new alert through the **Expo push service** to every active support user's registered phone.
4. On Android, Expo delivers through **Firebase Cloud Messaging (FCM)**. The app has its own Firebase project; its Android config file is referenced from `app.json`, and the FCM server credential is uploaded to Expo once with `eas credentials`.
5. A notification for one failed job carries the job id, so tapping it opens that job.
6. Phones Expo reports as no longer registered are removed automatically.

Push does **not** work in Expo Go or on an emulator - it needs a real phone and a built app.
Only the alert's short text and a job id are sent - no documents or customer data.

### Retry and Mark handled
The app calls the same endpoints as the website (`/jobs/{id}/gk2/approve`, `/jobs/{id}/extract`,
`/support/issues/{id}/handled`), so the server's rules are identical: ERP retry only on a failed
run, AI retry only on an extraction that never finished.

## Building the Android app (APK)
Builds run in the cloud on **EAS** (Expo Application Services). No Android Studio needed.

1. Install the EAS CLI **globally, from outside the app folder**: `npm install -g eas-cli`.
   Do not use `npx eas-cli` inside `support-app` - the folder's `.npmrc` breaks it on npm 11.
2. `eas login` with the Expo account that has access to the linked project (the project id is already in `app.json`).
3. Before each build, bump **`android.versionCode`** and **`extra.buildLabel`** in `app.json`. The build label is shown on the login and Home screens, so the team can see which build they have.
4. From the app folder: `eas build --platform android --profile preview`.
   The `preview` profile (in `eas.json`) makes an installable **APK**; `production` makes an app bundle for the Play Store.
5. EAS gives a link to the APK. Send it to the team; they open it on the phone and allow "install unknown apps" once.

Always keep using the **existing Android signing key** stored in EAS. Never create a new one in
`eas credentials` - a differently signed APK will not install over the old app.

### Build gotchas (already fixed - keep them)
- **`.easignore` at the repository root.** The EAS uploader does not follow the repo's allow-list
  style `.gitignore`; without `.easignore` the upload contained no app files and every build failed.
  It also excludes the backend, frontend, deploy folder and any signing key files.
- **`.npmrc` in the app folder with `legacy-peer-deps=true`.** The npm on EAS refused Expo SDK 57's
  optional peer versions (react-dom vs react) and stopped at "Install dependencies".

### Developing
- `npm install`, then `npx expo start` and scan the QR code with Expo Go (no push in Expo Go).
- Checks: `npx tsc --noEmit` and `npx expo-doctor`.
- Add packages with `npx expo install <package>` so versions match the SDK.

### iPhone
Not built yet. Needs a company Apple Developer account; then `eas build --platform ios` and
`eas submit --platform ios` to TestFlight.

## Business rules
- **Support and Super Admin logins only.** Reason: the app shows every customer's jobs.
- **Password stored only in secure storage, and only with Remember me.** Reason: the desk needs to stay signed in on call without weakening the phone's security.
- **Same endpoints as the website for actions.** Reason: one set of server rules, no phone-only behaviour.
- **Push only to active support users.** A deactivated user's phone stops getting alerts.

## Where in the code
| Part | Code |
|---|---|
| App | `logicopilot/mobile/support-app` - `app.json`, `eas.json`, `.npmrc`, `src/` |
| API client, login, refresh, Remember me | `src/lib/api.ts` - `login`, `reLogin`, `doRefresh`, `api`, `ensureSession`, `logout` |
| Auth state | `src/lib/auth.tsx` - `AuthProvider` |
| Push registration | `src/lib/push.ts` - `registerForPush` |
| Upload filter for EAS | `/.easignore` (repository root) |
| Backend API | `backend/app/api/v1/support_app.py` - `/support/app/home`, `/issues`, `/jobs/{id}`, `/jobs/{id}/notes`, `/known-issues`, `/devices`, `/devices/remove` |
| Push sender, known-issue keys | `backend/app/core/support_app.py` - `send_push`, `issue_key`, `default_title` |
| Tables | `backend/app/models/support_app.py` - `SupportDevice`, `SupportKnownIssue` |
| Alert loop that pushes | `backend/app/core/support_alerts.py` - `check_once`, `_push` |
| Bearer token login | `backend/app/core/deps.py` - `_bearer_token`; `backend/app/api/v1/auth.py` - `login`, `refresh` |

## Settings
- `app.json`: `extra.defaultServer`, `extra.buildLabel`, `extra.eas.projectId`, `android.versionCode`, `android.googleServicesFile`.
- `eas.json`: build profiles `preview` (APK) and `production` (app bundle).
- Session length on the server: `ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_MINUTES`.

## Design decisions
- **Expo / React Native** so one codebase can later serve iPhone too.
- **Own Firebase project and own Expo app**, kept apart from the Manager app.
- **Known issues grouped by cause** (for example "ERP refused field X", "ERP login failed", "ERP timed out") so the desk writes a fix once instead of per job.
