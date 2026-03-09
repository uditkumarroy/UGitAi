**Root Cause:** Two intentional `throw RuntimeException("Test crash from home screen")` statements were left in production code: one at the end of `MainActivity.onCreate()` (causing the app to crash on every single launch), and one inside the `Button`'s `onClick` lambda in `FirebaseStatusScreen` (causing a crash on every button tap). These were debug/test crash triggers that were never removed before shipping.

**Fix Applied:** Removed both `throw RuntimeException(...)` statements and the redundant `FirebaseCrashlytics.getInstance().log(...)` call that immediately preceded the throw in `onCreate()`. The Crashlytics log call inside the Button's `onClick` was retained (it is harmless), but the throw was removed. No other behaviour was changed.

**Files Changed:**
- `app/src/main/java/com/ugitai/MainActivity.kt`
