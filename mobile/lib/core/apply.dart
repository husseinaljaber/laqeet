import '../models/models.dart';
import 'format.dart';

/// Ready-to-send application messages, filled with the person's own name and education.
class ApplyMessages {
  ApplyMessages(this.job, this.user, {this.educationLabel});

  final Job job;
  final User? user;
  final String? educationLabel;

  String get _city => job.cities.isNotEmpty ? job.cities.first : (job.country == 'tr' ? 'تركيا' : 'سوريا');
  String get _org => job.employer != null ? 'لدى ${job.employer}' : 'لديكم';

  String get _quals {
    final parts = <String>[
      if (educationLabel != null && educationLabel!.isNotEmpty) 'شهادة $educationLabel',
      if (user?.profile.experienceYears != null && user!.profile.experienceYears! > 0)
        '${user!.profile.experienceYears} سنوات خبرة',
    ];
    return parts.isEmpty ? '' : ' أمتلك ${parts.join(' و')}.';
  }

  String get whatsapp {
    final name = user == null ? '' : 'اسمي ${user!.fullName}، و';
    return [
      'السلام عليكم ورحمة الله وبركاته،',
      '$nameأتواصل معكم للتقديم على شاغر «${job.title}» $_org ($_city) المنشور عبر منصة لقيت.$_quals',
      'أرفق لكم السيرة الذاتية للتفضل بالاطلاع، ويسعدني تواصلكم لتحديد موعد مقابلة. شكراً لكم.',
    ].join('\n');
  }

  String get emailSubject => 'التقدم لشاغر: ${job.title} — $_city${user == null ? '' : ' (${user!.fullName})'}';

  String get emailBody => [
        'السادة في قسم الموارد البشرية $_org المحترمين،',
        'تحية طيبة وبعد،',
        '',
        'أود التقدم بطلبي لشغل شاغر «${job.title}» في $_city.$_quals',
        'أرفق لكم سيرتي الذاتية المحدثة للاطلاع على مؤهلاتي وخبراتي، وأتطلع لفرصة إجراء مقابلة عمل.',
        '',
        'مع خالص التقدير والاحترام،',
        user?.fullName ?? '',
        user?.email ?? '',
      ].join('\n');

  /// wa.me link to the first mobile number, or the ad's own WhatsApp link.
  Uri? whatsappUri() {
    final p = job.phones.where(isMobile).firstOrNull;
    final text = Uri.encodeComponent(whatsapp);
    if (p != null) return Uri.parse('https://wa.me/${waNumber(p, job.country)}?text=$text');
    final url = job.applyUrl;
    if (url != null) {
      final m = RegExp(r'wa\.me/(\d{7,15})', caseSensitive: false).firstMatch(url);
      if (m != null) return Uri.parse('https://wa.me/${m.group(1)}?text=$text');
    }
    return null;
  }

  Uri? emailUri() => job.email == null
      ? null
      : Uri(scheme: 'mailto', path: job.email, query: 'subject=${Uri.encodeComponent(emailSubject)}&body=${Uri.encodeComponent(emailBody)}');

  Uri? siteUri() {
    final url = job.applyUrl;
    if (url == null || RegExp(r'wa\.me/|api\.whatsapp\.com/', caseSensitive: false).hasMatch(url)) return null;
    final u = Uri.tryParse(url);
    return (u != null && (u.scheme == 'https' || u.scheme == 'http')) ? u : null;
  }
}
