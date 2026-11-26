import 'package:material_ui/material_ui.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../core/api_client.dart';
import '../core/repository.dart';
import '../core/token_store.dart';
import '../models/models.dart';

enum SessionStatus { loading, guest, needsVerification, needsProfile, ready }

/// Who is using the app and where they are in sign-up. Screens listen to this; the router redirects from it.
class Session extends ChangeNotifier {
  Session({required this.repo, required this.tokens}) {
    repo.api.onSessionExpired = _expired;
  }

  final Repository repo;
  final TokenStore tokens;

  SessionStatus status = SessionStatus.loading;
  User? user;
  AppConfig? config;
  String? startupError;
  final Set<int> savedIds = {};

  bool get loggedIn => user != null;

  /// Opening the app: load the pickers' lists, then the account if a token is stored.
  Future<void> start() async {
    status = SessionStatus.loading;
    startupError = null;
    notifyListeners();
    try {
      config = await repo.config();
    } on ApiException catch (e) {
      startupError = e.message;
      notifyListeners();
      return;
    }
    if (await tokens.refreshToken() != null) {
      try {
        user = await repo.me();
        await _loadSaved();
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

  Future<void> logout() async {
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
