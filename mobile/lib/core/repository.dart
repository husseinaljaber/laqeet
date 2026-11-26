import '../models/models.dart';
import 'api_client.dart';

/// Every call the app makes, typed. Screens use this, never raw URLs.
class Repository {
  Repository(this.api);

  final ApiClient api;

  // ------------------------------------------------------------ catalog
  Future<AppConfig> config() async => AppConfig.fromJson(await api.get('/api/v1/config') as Map<String, dynamic>);

  // ------------------------------------------------------------ auth
  Future<Map<String, dynamic>> register({
    required String firstName,
    required String lastName,
    required String gender,
    required String email,
    required String password,
    required bool acceptedTerms,
  }) async =>
      await api.post('/api/v1/auth/register', body: {
        'first_name': firstName,
        'last_name': lastName,
        'gender': gender,
        'email': email,
        'password': password,
        'accepted_terms': acceptedTerms,
        'source': 'app',
      }) as Map<String, dynamic>;

  Future<Map<String, dynamic>> login(String email, String password) async =>
      await api.post('/api/v1/auth/login', body: {'email': email, 'password': password}) as Map<String, dynamic>;

  Future<void> logout(String refreshToken) => api.post('/api/v1/auth/logout', body: {'refresh_token': refreshToken});

  Future<User> verifyEmail(String code) async =>
      User.fromJson(await api.post('/api/v1/auth/verify-email', body: {'code': code}, auth: true) as Map<String, dynamic>);

  Future<void> resendVerification() => api.post('/api/v1/auth/resend-verification', auth: true);

  Future<void> forgotPassword(String email) => api.post('/api/v1/auth/forgot-password', body: {'email': email});

  Future<Map<String, dynamic>> resetPassword(String email, String code, String newPassword) async =>
      await api.post('/api/v1/auth/reset-password',
          body: {'email': email, 'code': code, 'new_password': newPassword}) as Map<String, dynamic>;

  // ------------------------------------------------------------ me
  Future<User> me() async => User.fromJson(await api.get('/api/v1/me', auth: true) as Map<String, dynamic>);

  Future<User> updateMe(Map<String, dynamic> body) async =>
      User.fromJson(await api.patch('/api/v1/me', body: body) as Map<String, dynamic>);

  Future<Map<String, dynamic>> changePassword(String current, String next) async =>
      await api.post('/api/v1/me/password', body: {'current_password': current, 'new_password': next}, auth: true)
          as Map<String, dynamic>;

  Future<void> deleteAccount(String password) => api.post('/api/v1/me/delete', body: {'password': password}, auth: true);

  // ------------------------------------------------------------ jobs
  Future<JobPage> jobs({
    String? query,
    String? education,
    bool lower = false,
    String? city,
    String country = 'sy',
    String? sector,
    String? field,
    bool recent = false,
    int offset = 0,
    int limit = 20,
  }) async =>
      JobPage.fromJson(await api.get('/api/jobs', query: {
        'q': query,
        'edu': education,
        'lower': lower ? 'true' : null,
        'city': city,
        'country': country,
        'sector': sector,
        'field': field,
        'sort': recent ? 'recent' : null,
        'offset': '$offset',
        'limit': '$limit',
      }) as Map<String, dynamic>);

  Future<Job> job(int id) async => Job.fromJson(await api.get('/api/jobs/$id') as Map<String, dynamic>);

  Future<void> report(int id, String reason) => api.post('/api/jobs/$id/report', body: {'reason': reason});

  // ------------------------------------------------------------ saved jobs
  Future<List<SavedItem>> saved() async {
    final data = await api.get('/api/v1/me/jobs', auth: true) as Map<String, dynamic>;
    return (data['items'] as List? ?? const []).whereType<Map<String, dynamic>>().map(SavedItem.fromJson).toList();
  }

  Future<void> setStage(int jobId, String stage, {String? note}) =>
      api.put('/api/v1/me/jobs/$jobId', body: {'stage': stage, 'note': ?note});

  Future<void> unsave(int jobId) => api.delete('/api/v1/me/jobs/$jobId');

  /// Anonymous counter (no account data): «قدّم» pressed, by method and source.
  Future<void> countApply(String method, String? source) async {
    try {
      await api.post('/api/e', body: {'e': 'apply', 'k': method, 's': source ?? ''});
    } catch (_) {}
  }
}
