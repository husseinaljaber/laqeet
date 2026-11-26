import 'package:material_ui/material_ui.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../core/api_client.dart';
import '../core/push.dart';
import '../core/repository.dart';
import '../core/token_store.dart';
import '../models/models.dart';

enum SessionStatus { loading, guest, needsVerification, needsProfile, ready }

/// Who is using the app and where they are in sign-up. Screens listen to this; the router redirects from it.
class Session extends ChangeNotifier {
  Session({required this.repo, required this.tokens, PushService? push}) : push = push ?? NoPush() {
    repo.api.onSessionExpired = _expired;
    this.push.tokenRefresh.listen((t) {
      if (user != null) _sendDevice(t);
    });
  }

  final Repository repo;
  final TokenStore tokens;
  final PushService push;
  String? _deviceToken;

  SessionStatus status = SessionStatus.loading;
  User? user;
  AppConfig? config;
  String? startupError;
  bool serverMissing = false; // phone build without a fixed address: ask for it

  static const _serverKey = 'laqeet.server';

  /// The address saved on this phone (first launch), if the build didn't fix one.
  Future<void> loadSavedServer() async {
    if (repo.api.hasServer) return;
    try {
      final saved = (await SharedPreferences.getInstance()).getString(_serverKey);
      if (saved != null && saved.isNotEmpty) repo.api.baseUrl = saved;
    } catch (_) {}
  }

  /// Try an address; keep it only if a Laqeet server answers there.
  Future<void> useServer(String address) async {
    final old = repo.api.webBase;
    repo.api.baseUrl = address;
    try {
      await repo.config();
    } catch (e) {
      repo.api.baseUrl = old;
      rethrow;
    }
    try {
      await (await SharedPreferences.getInstance()).setString(_serverKey, address);
    } catch (_) {}
    await start();
  }
  final Set<int> savedIds = {};

  bool get loggedIn => user != null;

  /// Opening the app: load the pickers' lists, then the account if a token is stored.
  Future<void> start() async {
    status = SessionStatus.loading;
    startupError = null;
    serverMissing = false;
    notifyListeners();
    await loadSavedServer();
    try {
      config = await repo.config();
    } on ApiException catch (e) {
      serverMissing = e.code == 'no_server';
      startupError = e.message;
      notifyListeners();
      return;
    }
    if (await tokens.refreshToken() != null) {
      try {
        user = await repo.me();
        await _loadSaved();
        _syncDevice();
      } on ApiException catch (e) {
        if (e.isUnauthorized) {
          await tokens.clear();
          user = null;
        } else {
          startupError = e.message;
          notifyListeners();
          return;
        }
      }
    }
    _setStatus();
  }

  Future<void> _applySession(Map<String, dynamic> s) async {
    await tokens.save(s['access_token'] as String, s['refresh_token'] as String);
    user = User.fromJson(s['user'] as Map<String, dynamic>);
    await _loadSaved();
    _setStatus();
    _syncDevice();
  }

  Future<void> login(String email, String password) async => _applySession(await repo.login(email, password));

  Future<void> register({
    required String firstName,
    required String lastName,
    required String gender,
    required String email,
    required String password,
    required bool acceptedTerms,
  }) async =>
      _applySession(await repo.register(
        firstName: firstName,
        lastName: lastName,
        gender: gender,
        email: email,
        password: password,
        acceptedTerms: acceptedTerms,
      ));

  Future<void> verify(String code) async {
    user = await repo.verifyEmail(code);
    _setStatus();
  }

  Future<void> resetPassword(String email, String code, String password) async =>
      _applySession(await repo.resetPassword(email, code, password));

  Future<void> changePassword(String current, String next) async =>
      _applySession(await repo.changePassword(current, next));

  Future<void> saveProfile(Profile p) async {
    user = await repo.updateMe({'profile': p.toJson()});
    _setStatus();
  }

  Future<void> updateName({required String firstName, required String lastName, required String gender}) async {
    user = await repo.updateMe({'first_name': firstName, 'last_name': lastName, 'gender': gender});
    notifyListeners();
  }

  /// Turn on phone notifications: asks permission and tells the server this phone's token. False if refused.
  Future<bool> enablePush() async {
    final t = await push.enable();
    if (t == null) return false;
    await _sendDevice(t);
    return true;
  }

  /// Permission was given before (on this phone): make sure the server knows the token for the current account.
  Future<void> _syncDevice() async {
    if (user == null || !push.available) return;
    try {
      final t = await push.currentToken();
      if (t != null) await _sendDevice(t);
    } catch (_) {}
  }

  Future<void> _sendDevice(String token) async {
    _deviceToken = token;
    try {
      await repo.addDevice(token, push.platform);
    } catch (_) {}
  }

  Future<void> logout() async {
    if (_deviceToken != null) {
      try {
        await repo.removeDevice(_deviceToken!); // this phone stops getting this account's alerts
      } catch (_) {}
      _deviceToken = null;
    }
    final refresh = await tokens.refreshToken();
    if (refresh != null) {
      try {
        await repo.logout(refresh);
      } catch (_) {}
    }
    await _signedOut();
  }

  Future<void> deleteAccount(String password) async {
    await repo.deleteAccount(password);
    await _signedOut();
  }

  /// Browse without an account (saving needs one).
  void continueAsGuest() {
    status = SessionStatus.ready;
    notifyListeners();
  }

  // ------------------------------------------------------------ saved jobs, kept in sync with the server
  bool isSaved(int jobId) => savedIds.contains(jobId);

  Future<void> toggleSaved(int jobId) async {
    final was = savedIds.contains(jobId);
    was ? savedIds.remove(jobId) : savedIds.add(jobId);
    notifyListeners();
    try {
      was ? await repo.unsave(jobId) : await repo.setStage(jobId, 'saved');
    } catch (_) {
      was ? savedIds.add(jobId) : savedIds.remove(jobId);
      notifyListeners();
      rethrow;
    }
  }

  Future<void> _loadSaved() async {
    savedIds.clear();
    if (user == null) return;
    try {
      savedIds.addAll((await repo.saved()).map((s) => s.jobId));
    } catch (_) {}
  }

  void refreshSaved() => _loadSaved().then((_) => notifyListeners());

  Future<void> _signedOut() {
    user = null;
    savedIds.clear();
    status = SessionStatus.guest;
    notifyListeners();
    return tokens.clear();
  }

  void _expired() {
    if (user != null) _signedOut();
  }

  void _setStatus() {
    final u = user;
    if (u == null) {
      status = status == SessionStatus.ready ? SessionStatus.ready : SessionStatus.guest;
    } else if (!u.emailVerified) {
      status = SessionStatus.needsVerification;
    } else if (!u.profile.complete) {
      status = SessionStatus.needsProfile;
    } else {
      status = SessionStatus.ready;
    }
    notifyListeners();
  }
}

/// Light / dark / follow the phone, remembered on this device.
class ThemeController extends ChangeNotifier {
  ThemeMode mode = ThemeMode.system;
  static const _key = 'laqeet.theme';

  Future<void> load() async {
    try {
      final v = (await SharedPreferences.getInstance()).getString(_key);
      mode = ThemeMode.values.firstWhere((m) => m.name == v, orElse: () => ThemeMode.system);
      notifyListeners();
    } catch (_) {}
  }

  Future<void> set(ThemeMode m) async {
    mode = m;
    notifyListeners();
    try {
      await (await SharedPreferences.getInstance()).setString(_key, m.name);
    } catch (_) {}
  }
}
