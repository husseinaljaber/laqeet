/// Arabic wording for dates and counts.
library;

String ago(String? iso, {DateTime? now}) {
  if (iso == null) return '';
  final t = DateTime.tryParse(iso);
  if (t == null) return '';
  final d = (now ?? DateTime.now()).toUtc().difference(t.toUtc());
  if (d.inMinutes < 60) return 'هلق';
  if (d.inHours < 24) {
    if (d.inHours == 1) return 'من ساعة';
    if (d.inHours == 2) return 'من ساعتين';
    return d.inHours <= 10 ? 'من ${d.inHours} ساعات' : 'من ${d.inHours} ساعة';
  }
  if (d.inDays == 1) return 'مبارح';
  if (d.inDays == 2) return 'من يومين';
  if (d.inDays < 11) return 'من ${d.inDays} أيام';
  if (d.inDays < 30) return 'من ${d.inDays} يوم';
  final m = d.inDays ~/ 30;
  return m == 1 ? 'من شهر' : (m == 2 ? 'من شهرين' : 'من $m شهور');
}

/// «باقي 3 أيام» for a deadline (yyyy-mm-dd); null when there is none or it's long gone.
String? deadlineText(String? date, {DateTime? now}) {
  if (date == null) return null;
  final t = DateTime.tryParse(date);
  if (t == null) return null;
  final today = now ?? DateTime.now();
  final days = DateTime(t.year, t.month, t.day).difference(DateTime(today.year, today.month, today.day)).inDays;
  if (days < 0) return 'انتهى التقديم';
  if (days == 0) return 'آخر يوم اليوم';
  if (days == 1) return 'آخر يوم بكرا';
  if (days == 2) return 'باقي يومين';
  if (days <= 10) return 'باقي $days أيام';
  return 'آخر موعد $date';
}

bool deadlineSoon(String? date, {DateTime? now}) {
  final t = date == null ? null : DateTime.tryParse(date);
  if (t == null) return false;
  final today = now ?? DateTime.now();
  final days = DateTime(t.year, t.month, t.day).difference(DateTime(today.year, today.month, today.day)).inDays;
  return days >= 0 && days <= 3;
}

String jobsCount(int n) {
  if (n == 0) return 'ما في وظائف';
  if (n == 1) return 'وظيفة وحدة';
  if (n == 2) return 'وظيفتين';
  if (n <= 10) return '$n وظائف';
  return '$n وظيفة';
}

/// Syrian/Turkish numbers to the international form for WhatsApp links.
String waNumber(String phone, String country) {
  var d = phone.replaceAll(RegExp(r'[^\d+]'), '');
  if (d.startsWith('+')) return d.substring(1);
  if (d.startsWith('00')) return d.substring(2);
  if (country == 'tr') {
    if (d.startsWith('90')) return d;
    if (d.startsWith('0')) d = d.substring(1);
    return '90$d';
  }
  if (d.startsWith('963')) return d;
  if (d.startsWith('0')) d = d.substring(1);
  return '963$d';
}

/// A number WhatsApp can reach (Syrian 09…, Turkish 05… / +905…).
bool isMobile(String phone) {
  final p = phone.replaceAll(RegExp(r'[\s-]'), '');
  return RegExp(r'^09\d{8}$').hasMatch(p) || RegExp(r'^\+?(963)?9\d{8}$').hasMatch(p) ||
      RegExp(r'^\+?905\d{9}$').hasMatch(p) || RegExp(r'^05\d{9}$').hasMatch(p);
}
