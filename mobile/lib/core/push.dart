import 'dart:async';

import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/foundation.dart';

/// Phone notifications for job alerts.
///
/// Firebase is set up from build-time values, so the app builds and runs without any Firebase files:
///   flutter build apk --dart-define=FIREBASE_API_KEY=… --dart-define=FIREBASE_APP_ID=…
///                     --dart-define=FIREBASE_SENDER_ID=… --dart-define=FIREBASE_PROJECT_ID=…
/// Without them (and on the web, and in tests) [NoPush] is used: alerts can still go to Telegram.
abstract class PushService {
  bool get available;

  /// Ask permission (Android 13+, iPhone) and return this phone's token, or null if refused/unavailable.
  Future<String?> enable();

  /// The token if permission was already given (no prompt).
  Future<String?> currentToken();

  Stream<String> get tokenRefresh;

  /// Screens to open when a notification is tapped (e.g. /job/123).
  Stream<String> get openedRoutes;

  /// The notification that launched the app, if any.
  Future<String?> initialRoute();

  /// A notification that arrived while the app was open: (title, body, route).
  Stream<(String, String, String)> get foreground;

  String get platform => defaultTargetPlatform == TargetPlatform.iOS ? 'ios' : 'android';
}

class NoPush extends PushService {
  @override
  bool get available => false;
  @override
  Future<String?> enable() async => null;
  @override
  Future<String?> currentToken() async => null;
  @override
  Stream<String> get tokenRefresh => const Stream.empty();
  @override
  Stream<String> get openedRoutes => const Stream.empty();
  @override
  Future<String?> initialRoute() async => null;
  @override
  Stream<(String, String, String)> get foreground => const Stream.empty();
}

const _apiKey = String.fromEnvironment('FIREBASE_API_KEY');
const _appId = String.fromEnvironment('FIREBASE_APP_ID');
const _senderId = String.fromEnvironment('FIREBASE_SENDER_ID');
const _projectId = String.fromEnvironment('FIREBASE_PROJECT_ID');

bool get firebaseConfigured => !kIsWeb && _apiKey.isNotEmpty && _appId.isNotEmpty && _senderId.isNotEmpty && _projectId.isNotEmpty;

/// The right service for this build: Firebase when configured and it starts, otherwise [NoPush].
Future<PushService> createPush() async {
  if (!firebaseConfigured) return NoPush();
  try {
    await Firebase.initializeApp(
      options: const FirebaseOptions(apiKey: _apiKey, appId: _appId, messagingSenderId: _senderId, projectId: _projectId),
    );
    return FirebasePush(FirebaseMessaging.instance);
  } catch (e) {
    debugPrint('Firebase did not start: $e');
    return NoPush();
  }
}

// Uses the classic registration token (getToken / onTokenRefresh): it is what the server's FCM HTTP v1 sender
// addresses (`message.token`). The newer register()/onRegistered API hands out installation IDs for a different
// send path; switch both sides together if that becomes necessary.
// ignore_for_file: deprecated_member_use
class FirebasePush extends PushService {
  FirebasePush(this._m);

  final FirebaseMessaging _m;

  static String? _route(RemoteMessage m) {
    final r = m.data['route'];
    return r is String && r.startsWith('/') ? r : null;
  }

  @override
  bool get available => true;

  @override
  Future<String?> enable() async {
    final s = await _m.requestPermission();
    if (s.authorizationStatus == AuthorizationStatus.denied) return null;
    return _m.getToken();
  }

  @override
  Future<String?> currentToken() async {
    final s = await _m.getNotificationSettings();
    if (s.authorizationStatus != AuthorizationStatus.authorized && s.authorizationStatus != AuthorizationStatus.provisional) {
      return null;
    }
    return _m.getToken();
  }

  @override
  Stream<String> get tokenRefresh => _m.onTokenRefresh;

  @override
  Stream<String> get openedRoutes => FirebaseMessaging.onMessageOpenedApp.map(_route).where((r) => r != null).cast<String>();

  @override
  Future<String?> initialRoute() async {
    final m = await _m.getInitialMessage();
    return m == null ? null : _route(m);
  }

  @override
  Stream<(String, String, String)> get foreground => FirebaseMessaging.onMessage.map(
        (m) => (m.notification?.title ?? '', m.notification?.body ?? '', _route(m) ?? '/jobs'),
      );
}
