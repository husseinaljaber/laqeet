import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// A tiny in-memory stand-in for the Laqeet API, enough for the app's flows.
class FakeServer {
  final users = <String, Map<String, dynamic>>{};
  final saved = <int, String>{};
  String? currentEmail;
  int accessVersion = 0;
  int refreshCalls = 0;
  bool expireNextAccess = false;
  final requests = <String>[];
  Map<String, String> lastJobsQuery = const {};

  static Map<String, dynamic> job(int id, {String edu = 'secondary', String title = 'محاسب'}) => {
        'id': id,
        'title': '$title $id',
        'employer': 'شركة الأمل',
        'cities': ['دمشق'],
        'education': edu,
        'kind': 'quick',
        'phones': ['0933123456'],
        'whatsapp': true,
        'sector': 'private',
        'country': 'sy',
        'posted_at': DateTime.now().toUtc().toIso8601String(),
        'sources': [
          {'label': 'قناة', 'post_url': 'https://t.me/x/1'},
        ],
      };

  Map<String, dynamic> get config => {
        'education': [
          {'code': 'none', 'label': 'بدون شهادة'},
          {'code': 'secondary', 'label': 'بكالوريا'},
          {'code': 'bachelor', 'label': 'جامعة'},
        ],
        'fields': [
          {'code': 'business', 'label': 'إدارة ومحاسبة'},
        ],
        'countries': [
          {'code': 'sy', 'label': 'سوريا', 'cities': ['دمشق', 'حلب']},
          {'code': 'tr', 'label': 'تركيا', 'cities': ['إسطنبول']},
        ],
        'stages': [
          {'code': 'saved', 'label': 'محفوظة'},
          {'code': 'applied', 'label': 'قدّمت'},
        ],
      };

  Map<String, dynamic> _user(String email) => users[email]!['public'] as Map<String, dynamic>;

  Map<String, dynamic> _session(String email) {
    currentEmail = email;
    accessVersion++;
    return {'access_token': 'a$accessVersion', 'refresh_token': 'r$accessVersion', 'user': _user(email)};
  }

  http.Response _json(Object body, [int status = 200]) =>
      http.Response.bytes(utf8.encode(jsonEncode(body)), status, headers: {'content-type': 'application/json'});

  late final client = MockClient((req) async {
    requests.add('${req.method} ${req.url.path}');
    final body = req.body.isEmpty ? <String, dynamic>{} : jsonDecode(req.body) as Map<String, dynamic>;
    final path = req.url.path;
    final authed = req.headers['Authorization'] == 'Bearer a$accessVersion' && currentEmail != null;
    if (path.startsWith('/api/v1/me') || path.startsWith('/api/v1/auth/verify') || path.startsWith('/api/v1/auth/resend')) {
      if (expireNextAccess) {
        expireNextAccess = false;
        return _json({'detail': 'expired'}, 401);
      }
      if (!authed) return _json({'detail': 'سجّل دخول'}, 401);
    }
    switch ((req.method, path)) {
      case ('GET', '/api/v1/config'):
        return _json(config);
      case ('POST', '/api/v1/auth/register'):
        final email = body['email'] as String;
        if (users.containsKey(email)) return _json({'detail': 'هالإيميل مسجّل عنا', 'field': 'email'}, 409);
        users[email] = {
          'password': body['password'],
          'public': {
            'id': users.length + 1,
            'email': email,
            'first_name': body['first_name'],
            'last_name': body['last_name'],
            'gender': body['gender'],
            'email_verified': false,
            'profile': null,
          },
        };
        return _json(_session(email), 201);
      case ('POST', '/api/v1/auth/login'):
        final u = users[body['email']];
        if (u == null || u['password'] != body['password']) {
          return _json({'detail': 'الإيميل أو كلمة السر غلط'}, 401);
        }
        return _json(_session(body['email'] as String));
      case ('POST', '/api/v1/auth/refresh'):
        refreshCalls++;
        if (body['refresh_token'] != 'r$accessVersion') return _json({'detail': 'no'}, 401);
        return _json(_session(currentEmail!));
      case ('POST', '/api/v1/auth/logout'):
        currentEmail = null;
        return _json({'ok': true});
      case ('POST', '/api/v1/auth/verify-email'):
        if (body['code'] != '123456') return _json({'detail': 'الكود غلط'}, 400);
        _user(currentEmail!)['email_verified'] = true;
        return _json(_user(currentEmail!));
      case ('GET', '/api/v1/me'):
        return _json(_user(currentEmail!));
      case ('PATCH', '/api/v1/me'):
        final u = _user(currentEmail!);
        if (body['profile'] != null) u['profile'] = body['profile'];
        for (final k in ['first_name', 'last_name', 'gender']) {
          if (body[k] != null) u[k] = body[k];
        }
        return _json(u);
      case ('GET', '/api/v1/me/jobs'):
        return _json({
          'items': [
            for (final e in saved.entries) {'job_id': e.key, 'stage': e.value, 'job': job(e.key)},
          ],
        });
      case ('GET', '/api/jobs'):
        final offset = int.parse(req.url.queryParameters['offset'] ?? '0');
        final q = req.url.queryParameters['q'];
        lastJobsQuery = req.url.queryParameters;
        final total = q == 'لاشي' ? 0 : 45;
        final n = (total - offset).clamp(0, 20);
        return _json({
          'total': total,
          'jobs': [for (var i = 0; i < n; i++) job(offset + i + 1)],
          // a tiny stand-in for the server's parser
          'understood': [
            if (q != null && q.contains('بدمشق')) {'kind': 'city', 'value': 'دمشق', 'label': 'دمشق', 'span': 'بدمشق'},
            if (q != null && q.contains('دوام جزئي')) {'kind': 'shift', 'value': 'part', 'label': 'دوام جزئي', 'span': 'دوام جزئي'},
          ],
          'corrected': q == 'محسب' ? 'محاسب' : null,
        });
    }
    if (req.method == 'GET' && path.startsWith('/api/jobs/')) return _json(job(int.parse(path.split('/').last)));
    if (path.startsWith('/api/v1/me/jobs/')) {
      final id = int.parse(path.split('/').last);
      if (req.method == 'PUT') {
        saved[id] = body['stage'] as String;
      } else {
        saved.remove(id);
      }
      return _json({'ok': true});
    }
    return _json({'detail': 'not found'}, 404);
  });
}
