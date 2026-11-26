import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import 'env.dart';
import 'token_store.dart';

/// A problem the server (or the network) reported, ready to show: [message] is Arabic, [field] names the input.
class ApiException implements Exception {
  ApiException(this.message, {this.status = 0, this.field, this.code});

  final String message;
  final int status;
  final String? field;
  final String? code;

  bool get isNetwork => status == 0;
  bool get isUnauthorized => status == 401;

  @override
  String toString() => 'ApiException($status, $code, $message)';
}

/// Talks JSON to the Laqeet API. Adds the login token, renews it once when it expires, and turns every failure
/// into an [ApiException] with a message people understand.
class ApiClient {
  ApiClient({required this.tokens, http.Client? httpClient, String? baseUrl})
      : _http = httpClient ?? http.Client(),
        _base = baseUrl ?? apiBase();

  final TokenStore tokens;
  final http.Client _http;
  final String _base;

  /// Called when the session can't be renewed (logged out elsewhere, password changed, expired).
  void Function()? onSessionExpired;

  Future<String?>? _refreshing;
  static const _timeout = Duration(seconds: 20);

  Uri uri(String path, [Map<String, String?>? query]) {
    final q = <String, String>{};
    query?.forEach((k, v) {
      if (v != null && v.isNotEmpty) q[k] = v;
    });
    return Uri.parse('$_base$path').replace(queryParameters: q.isEmpty ? null : q);
  }

  String get webBase => _base;

  Future<dynamic> get(String path, {Map<String, String?>? query, bool auth = false}) =>
      _send('GET', path, query: query, auth: auth);

  Future<dynamic> post(String path, {Object? body, bool auth = false}) => _send('POST', path, body: body, auth: auth);

  Future<dynamic> put(String path, {Object? body}) => _send('PUT', path, body: body, auth: true);

  Future<dynamic> patch(String path, {Object? body}) => _send('PATCH', path, body: body, auth: true);

  Future<dynamic> delete(String path) => _send('DELETE', path, auth: true);

  Future<dynamic> _send(String method, String path,
      {Map<String, String?>? query, Object? body, bool auth = false, bool retried = false}) async {
    final headers = <String, String>{'Accept': 'application/json', 'X-Device': 'laqeet-app/$appVersion'};
    if (body != null) headers['Content-Type'] = 'application/json';
    if (auth) {
      final token = await tokens.accessToken();
      if (token != null) headers['Authorization'] = 'Bearer $token';
    }
    final req = http.Request(method, uri(path, query))..headers.addAll(headers);
    if (body != null) req.body = jsonEncode(body);
    http.Response res;
    try {
      res = await http.Response.fromStream(await _http.send(req).timeout(_timeout));
    } on TimeoutException {
      throw ApiException('النت بطيء كتير، جرّب كمان مرة');
    } catch (_) {
      throw ApiException('ما في اتصال بالإنترنت، تأكد من النت وجرّب كمان مرة');
    }
    if (res.statusCode == 401 && auth && !retried && path != '/api/v1/auth/refresh') {
      final renewed = await _renew();
      if (renewed != null) return _send(method, path, query: query, body: body, auth: auth, retried: true);
      onSessionExpired?.call();
    }
    final text = utf8.decode(res.bodyBytes);
    final data = text.isEmpty ? null : _tryJson(text);
    if (res.statusCode >= 200 && res.statusCode < 300) return data;
    final map = data is Map ? data : const {};
    final detail = map['detail'];
    throw ApiException(
      detail is String && detail.isNotEmpty ? detail : _fallbackMessage(res.statusCode),
      status: res.statusCode,
      field: map['field'] as String?,
      code: map['code'] as String?,
    );
  }

  /// One renewal at a time, shared by every request that hit an expired token together.
  Future<String?> _renew() {
    return _refreshing ??= () async {
      try {
        final refresh = await tokens.refreshToken();
        if (refresh == null) return null;
        final data = await _send('POST', '/api/v1/auth/refresh', body: {'refresh_token': refresh}, retried: true);
        await tokens.save(data['access_token'] as String, data['refresh_token'] as String);
        return data['access_token'] as String;
      } catch (_) {
        await tokens.clear();
        return null;
      } finally {
        _refreshing = null;
      }
    }();
  }

  static dynamic _tryJson(String s) {
    try {
      return jsonDecode(s);
    } catch (_) {
      return null;
    }
  }

  static String _fallbackMessage(int status) {
    if (status == 429) return 'محاولات كتير، استنى شوي وجرّب';
    if (status >= 500) return 'في مشكلة عنا بالسيرفر، جرّب بعد شوي';
    if (status == 404) return 'ما لقينا هالشي، يمكن انحذف';
    return 'صار خطأ، جرّب كمان مرة';
  }
}
