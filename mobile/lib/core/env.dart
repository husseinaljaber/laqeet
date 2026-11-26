/// Where the app finds the Laqeet server.
///
/// Build with:  flutter build apk --dart-define=API_BASE=https://your-domain
/// On the web build served by the Laqeet server itself, leave it empty: the page's own address is used.
/// A phone build without API_BASE asks for the site's address on first launch (saved on the phone), so one APK
/// can be tried against any server.
library;

const String _apiBase = String.fromEnvironment('API_BASE', defaultValue: '');

/// The address chosen at build time (or the web page's own), or '' when the phone has to ask.
String apiBase() {
  if (_apiBase.isNotEmpty) return normalizeServer(_apiBase)!;
  final base = Uri.base;
  if (base.scheme == 'http' || base.scheme == 'https') return base.origin;
  return '';
}

/// True when the address was fixed at build time (or by the web page) and must not be changed in the app.
bool get serverFixed => _apiBase.isNotEmpty || Uri.base.scheme == 'http' || Uri.base.scheme == 'https';

/// «laqeet.example/» → «https://laqeet.example». Null if it isn't a usable address.
/// Plain http is only accepted for local test servers (the phone refuses it elsewhere anyway).
String? normalizeServer(String input) {
  var s = input.trim();
  if (s.isEmpty) return null;
  if (!s.contains('://')) s = 'https://$s';
  final u = Uri.tryParse(s);
  if (u == null || u.host.isEmpty || !(u.scheme == 'https' || u.scheme == 'http')) return null;
  final local = u.host == 'localhost' || u.host == '10.0.2.2' || RegExp(r'^(192\.168|10\.|172\.(1[6-9]|2\d|3[01]))\.').hasMatch(u.host);
  if (u.scheme == 'http' && !local) return null;
  return '${u.scheme}://${u.host}${u.hasPort ? ':${u.port}' : ''}';
}

const String appVersion = String.fromEnvironment('APP_VERSION', defaultValue: '1.0.0');
