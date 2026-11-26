import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Login tokens, kept in the phone's secure storage (Keychain on iPhone, Keystore on Android).
abstract class TokenStore {
  Future<String?> accessToken();
  Future<String?> refreshToken();
  Future<void> save(String access, String refresh);
  Future<void> clear();
}

class SecureTokenStore implements TokenStore {
  SecureTokenStore([FlutterSecureStorage? storage]) : _s = storage ?? const FlutterSecureStorage();

  final FlutterSecureStorage _s;
  static const _a = 'laqeet.access', _r = 'laqeet.refresh';
  String? _accessCache;

  @override
  Future<String?> accessToken() async => _accessCache ??= await _s.read(key: _a);

  @override
  Future<String?> refreshToken() => _s.read(key: _r);

  @override
  Future<void> save(String access, String refresh) async {
    _accessCache = access;
    await _s.write(key: _a, value: access);
    await _s.write(key: _r, value: refresh);
  }

  @override
  Future<void> clear() async {
    _accessCache = null;
    await _s.delete(key: _a);
    await _s.delete(key: _r);
  }
}

/// For tests.
class MemoryTokenStore implements TokenStore {
  String? access, refresh;

  @override
  Future<String?> accessToken() async => access;

  @override
  Future<String?> refreshToken() async => refresh;

  @override
  Future<void> save(String a, String r) async {
    access = a;
    refresh = r;
  }

  @override
  Future<void> clear() async {
    access = null;
    refresh = null;
  }
}
