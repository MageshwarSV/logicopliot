# Cargora Support (phone app)

The Production Support desk on a phone: health in one line, today's failed / delayed jobs,
the problem list with the reason in plain words, Retry, Mark handled, notes with a status,
and Known issues (the fix written once per cause). Push alerts for failed jobs, delays,
stuck queues and anything DOWN.

Log in with a **support** user (same as the website). The server defaults to production;
tap "Server ... change" on the login screen to use SIT.

Backend: `backend/app/api/v1/support_app.py` (`/api/v1/support/app/*`), push sender
`backend/app/core/support_app.py`, called from the alert loop in `support_alerts.py`.

## Run on a phone while developing

    npm install
    npx expo start

Scan the QR code with **Expo Go** (Android) or the Camera (iPhone). Push alerts do not work in
Expo Go - they need a build (below).

## Build the Android app (APK) - one time setup

1. Create a free Expo account (company email) at expo.dev.
2. `npx eas-cli@latest login`
3. `npx eas-cli@latest init` - links this folder to an Expo project and writes
   `extra.eas.projectId` into app.json (commit that change).
4. `npx eas-cli@latest build --platform android --profile preview`
5. EAS gives a link to the .apk - send it to the team; they open it on the phone and install
   (allow "install unknown apps" once).

Push alerts on Android also need Firebase (FCM) credentials uploaded once:
`npx eas-cli@latest credentials` -> Android -> Google Service Account / FCM V1 key.

## iPhone (later)

Needs an Apple Developer account (99 USD / year, company). Then
`npx eas-cli@latest build --platform ios` and `npx eas-cli@latest submit --platform ios` to
TestFlight; the team installs via the TestFlight app.

## Checks

    npx tsc --noEmit
    npx expo-doctor
