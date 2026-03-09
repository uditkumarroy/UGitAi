**Root Cause:** In `MainActivity.onCreate()`, two lines were left in production code after testing:
1. `FirebaseCrashlytics.getInstance().log("Manual test crash from home screen")`
2. `throw RuntimeException("Test crash from home screen")`

The `throw RuntimeException(...)` unconditionally crashed the app every single time `onCreate` was called — i.e., on every app launch. This is the direct cause of the Firebase crash report. Additionally, the Button's `onClick` also called `throw RuntimeException(...)` directly on the main thread, which would crash the app whenever the button was tapped.

**Fix Applied:** 1. Removed the unconditional `throw RuntimeException("Test crash from home screen")` and its preceding `log()` call from `MainActivity.onCreate()` entirely — these were debug/test lines that must never ship to production.
2. In the Button's `onClick`, replaced `throw RuntimeException(...)` with `FirebaseCrashlytics.getInstance().recordException(...)` so it logs a non-fatal exception to Crashlytics without crashing the app. This preserves the intent of having a "test" button while preventing a real crash.

**Files Changed:**
- `app/src/main/java/com/ugitai/MainActivity.kt`
