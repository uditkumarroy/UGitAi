**Root Cause:** In `MainActivity.onCreate`, two leftover test/debug lines were accidentally shipped to production:
```kotlin
FirebaseCrashlytics.getInstance().log("Manual test crash from home screen")
throw RuntimeException("Test crash from home screen")
```
These lines execute unconditionally on every app launch, crashing the app immediately after `setContent` returns. This produced the Crashlytics event `0f072cb2916dbed74954ed032e834b42`. The BigQuery 403 error in the issue description is a tooling/permissions side-effect of trying to look up the crash, not the root cause itself.

**Fix Applied:** Removed the two unconditional test-crash lines (`FirebaseCrashlytics.getInstance().log(...)` + `throw RuntimeException(...)`) from the end of `MainActivity.onCreate`. The intentional crash button in `FirebaseStatusScreen` is user-triggered and explicitly labeled "Force Crash (Test)", so it is left in place as-is per the minimal-change rule.

**Files Changed:**
- `app/src/main/java/com/ugitai/MainActivity.kt`
