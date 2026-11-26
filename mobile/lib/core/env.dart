/// Where the app finds the Laqeet server.
///
/// Build with:  flutter build apk --dart-define=API_BASE=https://your-domain
/// On the web build served by the Laqeet server itself, leave it empty: the page's own address is used.
library;

const String _apiBase = String.fromEnvironment('API_BASE', defaultValue: '');

String apiBase() {
  if (_apiBase.isNotEmpty) return _apiBase.replaceAll(RegExp(r'/+$'), '');
  final base = Uri.base;
  if (base.scheme == 'http' || base.scheme == 'https') return base.origin;
  return 'http://10.0.2.2:8000'; // Android emulator → the developer's machine
}

const String appVersion = String.fromEnvironment('APP_VERSION', defaultValue: '1.0.0');
