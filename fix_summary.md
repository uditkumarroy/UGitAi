**Root Cause:** The production crash (Firebase Issue `0f072cb2916dbed74954ed032e834b42`) was caused by deliberate test code left in `MainActivity.onCreate()`. After setting up the UI, the code unconditionally executed `throw RuntimeException("Test crash from home screen")`, crashing the app every single time it launched. A second crash path also existed inside the "Force Crash (Test)" `Button`'s `onClick` handler in `FirebaseStatusScreen`, which would crash the app whenever the user tapped that button.

**Fix Applied:** Removed both intentional crash sites:
1. Deleted `FirebaseCrashlytics.getInstance().log("Manual test crash from home screen")` and `throw RuntimeException("Test crash from home screen")` from the end of `MainActivity.onCreate()`.
2. Removed the "Force Crash (Test)" `Button` (and its `onClick` crash) from `FirebaseStatusScreen`, along with the now-unused `Spacer`, `height`, and `Button` imports.

**Files Changed:**
- `app/src/main/java/com/ugitai/MainActivity.kt`
