import 'package:material_ui/material_ui.dart';
import 'package:provider/provider.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../core/api_client.dart';
import '../../models/models.dart';
import '../../state/session.dart';
import '../../widgets/common.dart';

/// When and where job alerts arrive: daily summary / instant / off, phone notifications, Telegram.
class AlertsScreen extends StatefulWidget {
  const AlertsScreen({super.key});

  @override
  State<AlertsScreen> createState() => _AlertsScreenState();
}

class _AlertsScreenState extends State<AlertsScreen> with WidgetsBindingObserver {
  AlertSettings? _s;
  String? _error;
  bool _busy = false;
  bool _pushRefused = false;

  static String hourLabel(int h) => switch (h) {
        8 => '٨ الصبح',
        12 => '١٢ الظهر',
        16 => '٤ العصر',
        19 => '٧ المسا',
        21 => '٩ بالليل',
        _ => '$h:00',
      };

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _load();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  /// Back from Telegram: show whether the link worked.
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _load();
  }

  Future<void> _load() async {
    try {
      final s = await context.read<Session>().repo.alerts();
      if (mounted) {
        setState(() {
          _s = s;
          _error = null;
        });
      }
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  Future<void> _run(Future<void> Function() work) async {
    setState(() => _busy = true);
    try {
      await work();
    } catch (e) {
      if (mounted) toast(context, errorText(e), error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _save({String? mode, int? hour}) => _run(() async {
        final s = await context.read<Session>().repo.saveAlerts(mode: mode, dailyHour: hour);
        if (mounted) setState(() => _s = s);
      });

  Future<void> _enablePush() => _run(() async {
        final ok = await context.read<Session>().enablePush();
        if (!mounted) return;
        setState(() => _pushRefused = !ok);
        if (ok) toast(context, 'تمام، رح نخبرك ✓');
        await _load();
      });

  Future<void> _linkTelegram() => _run(() async {
        final url = await context.read<Session>().repo.telegramLink();
        await launchUrl(Uri.parse(url), mode: LaunchMode.externalApplication);
      });

  Future<void> _unlinkTelegram() => _run(() async {
        await context.read<Session>().repo.unlinkTelegram();
        await _load();
      });

  @override
  Widget build(BuildContext context) {
    final s = _s;
    final t = Theme.of(context);
    final push = context.read<Session>().push;
    if (s == null) {
      return Scaffold(
        appBar: AppBar(title: const Text('التنبيهات')),
        body: _error != null
            ? MessageView(icon: Icons.wifi_off_rounded, title: 'ما قدرنا نجيب الإعدادات', body: _error, action: 'جرّب كمان مرة', onAction: _load)
            : const Center(child: CircularProgressIndicator()),
      );
    }
    final muted = TextStyle(color: t.colorScheme.onSurfaceVariant);
    return FormPage(title: 'التنبيهات', children: [
      Text('منخبرك بالوظائف الجديدة يلي على قد شهادتك وبمدنك، كل وظيفة مرة وحدة بس.', style: muted.copyWith(height: 1.6)),
      const SizedBox(height: 20),
      Text('كيف بدك نخبرك؟', style: t.textTheme.titleMedium),
      const SizedBox(height: 10),
      SegmentedButton<String>(
        segments: const [
          ButtonSegment(value: 'daily', label: Text('ملخص يومي'), icon: Icon(Icons.today_outlined)),
          ButtonSegment(value: 'instant', label: Text('فوراً'), icon: Icon(Icons.bolt)),
          ButtonSegment(value: 'off', label: Text('موقّفة'), icon: Icon(Icons.notifications_off_outlined)),
        ],
        selected: {s.mode},
        onSelectionChanged: _busy ? null : (v) => _save(mode: v.first),
      ),
      const SizedBox(height: 8),
      Text(
        switch (s.mode) {
          'daily' => 'إشعار واحد باليوم فيه كل الجديد.',
          'instant' => 'أول ما تنزل وظيفة بتناسبك. ما منبعت شي بين ١٠ بالليل و٨ الصبح، ولحد ٦ مرات باليوم.',
          _ => 'ما رح يوصلك شي. فيك ترجع تشغّلها بأي وقت.',
        },
        style: t.textTheme.bodySmall?.copyWith(color: t.colorScheme.onSurfaceVariant),
      ),
      if (s.mode == 'daily') ...[
        const SizedBox(height: 18),
        Text('أيمتى يوصلك الملخص؟', style: t.textTheme.titleSmall),
        const SizedBox(height: 8),
        Wrap(spacing: 8, runSpacing: 8, children: [
          for (final h in s.hours)
            ChoiceChip(label: Text(hourLabel(h)), selected: s.dailyHour == h, onSelected: _busy ? null : (_) => _save(hour: h)),
        ]),
      ],
      const Divider(height: 40),
      Row(children: [
        const Icon(Icons.phone_android),
        const SizedBox(width: 10),
        Expanded(child: Text('إشعارات عالموبايل', style: t.textTheme.titleMedium)),
      ]),
      const SizedBox(height: 10),
      if (!push.available)
        Text('إشعارات الموبايل رح تشتغل قريباً. لهلق فيك تستلم التنبيهات عالتلغرام.', style: muted)
      else if (s.pushDevices > 0 && !_pushRefused)
        Row(children: [
          const Icon(Icons.check_circle, color: Colors.green, size: 20),
          const SizedBox(width: 8),
          Expanded(child: Text(s.mode == 'off' ? 'الجهاز جاهز، بس التنبيهات موقّفة.' : 'شغّالة على هالجهاز.')),
        ])
      else ...[
        BusyButton(label: 'شغّل الإشعارات', icon: Icons.notifications_active_outlined, busy: _busy, onPressed: _enablePush),
        if (_pushRefused)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text('الإشعارات مسكّرة لهالتطبيق. فيك تفتحها من إعدادات الموبايل ← التطبيقات ← لقيت ← الإشعارات.',
                style: TextStyle(color: t.colorScheme.error)),
          ),
      ],
      if (s.telegramAvailable) ...[
        const Divider(height: 40),
        Row(children: [
          const Icon(Icons.send_outlined),
          const SizedBox(width: 10),
          Expanded(child: Text('تلغرام', style: t.textTheme.titleMedium)),
        ]),
        const SizedBox(height: 10),
        if (s.telegramLinked) ...[
          const Row(children: [
            Icon(Icons.check_circle, color: Colors.green, size: 20),
            SizedBox(width: 8),
            Expanded(child: Text('مربوط: نفس التنبيهات بتوصلك عالتلغرام كمان.')),
          ]),
          TextButton(onPressed: _busy ? null : _unlinkTelegram, child: const Text('فك الربط')),
        ] else ...[
          Text('بيفتحلك بوت لقيت بالتلغرام، اكبس «Start» وبيصير يوصلك نفس الشي هونيك.', style: muted),
          const SizedBox(height: 10),
          OutlinedButton.icon(onPressed: _busy ? null : _linkTelegram, icon: const Icon(Icons.link), label: const Text('اربط التلغرام')),
        ],
      ],
    ]);
  }
}
