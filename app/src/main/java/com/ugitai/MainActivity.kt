package com.ugitai

import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.tooling.preview.Preview
import com.google.firebase.FirebaseApp
import com.google.firebase.crashlytics.FirebaseCrashlytics
import com.ugitai.ui.theme.UGitAiTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        val firebaseStatus = testFirebaseConnection()

        setContent {
            UGitAiTheme {
                Scaffold(modifier = Modifier.fillMaxSize()) { innerPadding ->
                    FirebaseStatusScreen(
                        status = firebaseStatus,
                        modifier = Modifier.padding(innerPadding)
                    )
                }
            }
        }
    }

    private fun testFirebaseConnection(): String {
        return try {
            val app = FirebaseApp.getInstance()
            val crashlytics = FirebaseCrashlytics.getInstance()
            crashlytics.log("Firebase connection test: OK")
            crashlytics.setCustomKey("connection_test", true)
            Log.i("Firebase", "Connected to project: ${app.options.projectId}")
            "Firebase connected\nProject: ${app.options.projectId}"
        } catch (e: Exception) {
            Log.e("Firebase", "Connection failed: ${e.message}")
            "Firebase connection failed:\n${e.message}"
        }
    }
}

@Composable
fun FirebaseStatusScreen(status: String, modifier: Modifier = Modifier) {
    Column(
        modifier = modifier.fillMaxSize(),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Text(text = status, style = MaterialTheme.typography.bodyLarge)
        Spacer(modifier = Modifier.height(16.dp))
        Button(
            onClick = {
                FirebaseCrashlytics.getInstance().log("Manual test crash from home screen")
                throw RuntimeException("Test crash from home screen")
            }
        ) {
            Text(text = "Force Crash (Test)")
        }
    }
}

@Preview(showBackground = true)
@Composable
fun FirebaseStatusPreview() {
    UGitAiTheme {
        FirebaseStatusScreen(status = "Firebase connected\nProject: test-firebase-b96cd")
    }
}
