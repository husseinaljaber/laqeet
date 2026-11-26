import 'package:flutter/services.dart';
import 'package:go_router/go_router.dart';
import 'package:material_ui/material_ui.dart';
import 'package:provider/provider.dart';
import 'package:share_plus/share_plus.dart';
import 'package:url_launcher/url_launcher.dart';

import '../core/api_client.dart';
import '../core/apply.dart';
import '../core/format.dart';
import '../core/theme.dart';
import '../models/models.dart';
import '../state/session.dart';
import '../widgets/common.dart';
import '../widgets/job_card.dart';

class JobDetailsScreen extends StatefulWidget {
  const JobDetailsScreen({super.key, required this.id, this.initial});

  final int id;
  final Job? initial;

  @override
  State<JobDetailsScreen> createState() => _JobDetailsScreenState();
}

class _JobDetailsScreenState extends State<JobDetailsScreen> {
  late Job? _job = widget.initial;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final j = await context.read<Session>().repo.job(widget.id);
      if (mounted) setState(() => _job = j);
    } on ApiException catch (e) {
      if (mounted && _job == null) setState(() => _error = e.status == 404 ? 'هالإعلان انشال أو مو موجود' : e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final job = _job;
    return Scaffold(
      appBar: AppBar(
        actions: job == null
            ? null
            : [
                _SaveButton(jobId: job.id),
                IconButton(tooltip: 'شارك', icon: const Icon(Icons.share_outlined), onPressed: () => _share(job)),
                PopupMenuButton<String>(
                  tooltip: 'المزيد',
                  onSelected: (v) => v == 'report' ? _report(job) : null,
                  itemBuilder: (_) => const [
                    PopupMenuItem(value: 'report', child: ListTile(leading: Icon(Icons.flag_outlined), title: Text('بلّغ عن الإعلان'))),
                  ],
                ),
              ],
      ),
      body: job == null
          ? (_error != null
              ? MessageView(icon: Icons.error_outline, title: _error!, action: 'جرّب كمان مرة', onAction: _load)
              : const Center(child: CircularProgressIndicator()))
          : _Details(job: job),
    );
  }

  void _share(Job job) {
    final base = context.read<Session>().repo.api.webBase;
    SharePlus.instance.share(ShareParams(text: '${job.title}${job.employer == null ? '' : ' — ${job.employer}'}\n$base/j/${job.id}'));
  }

  Future<void> _report(Job job) async {
    final reason = await showModalBottomSheet<String>(
      context: context,
      showDragHandle: true,
      builder: (ctx) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          const Padding(padding: EdgeInsets.only(bottom: 8), child: Text('شو المشكلة بهالإعلان؟', style: TextStyle(fontWeight: FontWeight.w700))),
          for (final (code, label) in const [
            ('scam', 'نصب أو طلبوا مصاري'),
            ('expired', 'الوظيفة انتهت'),
            ('wrong', 'المعلومات غلط'),
            ('not_job', 'مو إعلان شغل'),
          ])
            ListTile(title: Text(label), onTap: () => Navigator.pop(ctx, code)),
        ]),
      ),
    );
    if (reason == null || !mounted) return;
    try {
      await context.read<Session>().repo.report(job.id, reason);
      if (mounted) toast(context, 'شكراً، وصلنا البلاغ ومنراجعه');
    } catch (e) {
      if (mounted) toast(context, errorText(e), error: true);
    }
  }
}

class _SaveButton extends StatelessWidget {
  const _SaveButton({required this.jobId});

  final int jobId;

  @override
  Widget build(BuildContext context) {
    final saved = context.watch<Session>().isSaved(jobId);
    return IconButton(
      tooltip: saved ? 'شيل من المحفوظات' : 'احفظ',
      icon: Icon(saved ? Icons.bookmark : Icons.bookmark_border),
      onPressed: () => toggleSave(context, jobId),
    );
  }
}

class _Details extends StatelessWidget {
  const _Details({required this.job});

  final Job job;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final s = context.watch<Session>();
    final cfg = s.config;
    final fit = fitFor(job, s.user, cfg);
    final deadline = deadlineText(job.deadline);
    final muted = TextStyle(color: t.colorScheme.onSurfaceVariant);
    final facts = <(IconData, String, String)>[
      (Icons.school_outlined, 'الشهادة', cfg?.educationLabel(job.education) ?? ''),
      if (job.experience != null) (Icons.timeline, 'الخبرة', job.experience == 0 ? 'بلا خبرة' : '${job.experience}+ سنين'),
      if (job.salary != null) (Icons.payments_outlined, 'الراتب', job.salary!),
      if (job.shift != null) (Icons.schedule, 'الدوام', job.shift!),
      if (job.gender != null) (Icons.person_outline, 'مطلوب', job.gender == 'female' ? 'إناث' : (job.gender == 'male' ? 'ذكور' : job.gender!)),
      if (job.ageMin != null || job.ageMax != null)
        (Icons.cake_outlined, 'العمر', [if (job.ageMin != null) 'من ${job.ageMin}', if (job.ageMax != null) 'لـ ${job.ageMax}'].join(' ')),
      if (deadline != null) (Icons.event_outlined, 'التقديم', deadline),
    ];
    return Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 720),
        child: ListView(padding: const EdgeInsets.fromLTRB(20, 4, 20, 40), children: [
          Wrap(spacing: 6, runSpacing: 6, children: [
            if (job.official) const Tag('رسمية', icon: Icons.verified_outlined, color: AppColors.green),
            Tag(job.sector == 'gov' ? 'حكومي' : (job.sector == 'ngo' ? 'منظمة' : 'خاص'), icon: Icons.apartment_outlined),
            if (job.postedAt != null) Tag(ago(job.postedAt), icon: Icons.access_time),
          ]),
          const SizedBox(height: 12),
          Text(job.title, style: t.textTheme.headlineSmall),
          const SizedBox(height: 6),
          Text([job.employer, if (job.cities.isNotEmpty) job.cities.join('، ')].whereType<String>().join(' · '),
              style: t.textTheme.bodyLarge?.copyWith(color: t.colorScheme.onSurfaceVariant)),
          if (fit != Fit.unknown) ...[
            const SizedBox(height: 16),
            _FitBanner(fit: fit, need: cfg!.educationLabel(job.education), have: cfg.educationLabel(s.user!.profile.education)),
          ],
          const SizedBox(height: 16),
          Card(
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 6),
              child: Column(children: [
                for (final (icon, label, value) in facts)
                  ListTile(
                    dense: true,
                    leading: Icon(icon, size: 20),
                    title: Text(label, style: muted),
                    trailing: ConstrainedBox(
                      constraints: const BoxConstraints(maxWidth: 220),
                      child: Text(value, textAlign: TextAlign.end, style: const TextStyle(fontWeight: FontWeight.w600)),
                    ),
                  ),
              ]),
            ),
          ),
          if (job.summary != null && job.summary!.trim().isNotEmpty) ...[
            const SizedBox(height: 20),
            Text('عن الوظيفة', style: t.textTheme.titleMedium),
            const SizedBox(height: 8),
            SelectableText(job.summary!, style: const TextStyle(height: 1.7)),
          ],
          if (job.tasks.isNotEmpty) _Bullets(title: 'المهام', items: job.tasks),
          if (job.skills.isNotEmpty) _Bullets(title: 'المطلوب', items: job.skills),
          const SizedBox(height: 24),
          _ApplySection(job: job),
          if (s.isSaved(job.id)) ...[const SizedBox(height: 16), _StagePicker(jobId: job.id)],
          const SizedBox(height: 20),
          Container(
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(color: AppColors.warn.withValues(alpha: 0.08), borderRadius: BorderRadius.circular(12)),
            child: const Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Icon(Icons.shield_outlined, color: AppColors.warn, size: 20),
              SizedBox(width: 10),
              Expanded(child: Text('انتبه: الشغل الحقيقي ما بيطلب منك مصاري لتتوظف. إذا حدا طلب رسوم أو تحويل، لا تدفع وبلّغ عن الإعلان.')),
            ]),
          ),
          if (job.country == 'tr') ...[
            const SizedBox(height: 12),
            Card(
              child: ListTile(
                leading: const Icon(Icons.gavel_outlined),
                title: const Text('سوري وبدك تشتغل بتركيا؟'),
                subtitle: const Text('شو تغيّر بقواعد إذن العمل، وكيف تحمي حقك (التأمين SGK)'),
                trailing: const Icon(Icons.open_in_new, size: 18),
                onTap: () => launchUrl(Uri.parse('${s.repo.api.webBase}/turkey-work.html'), mode: LaunchMode.externalApplication),
              ),
            ),
          ],
          if (job.sources.isNotEmpty) ...[
            const SizedBox(height: 20),
            Text('المصدر', style: t.textTheme.titleSmall),
            for (final src in job.sources)
              ListTile(
                contentPadding: EdgeInsets.zero,
                leading: const Icon(Icons.link, size: 20),
                title: Text(src.label),
                onTap: src.url.startsWith('http') ? () => launchUrl(Uri.parse(src.url), mode: LaunchMode.externalApplication) : null,
              ),
          ],
        ]),
      ),
    );
  }
}

class _FitBanner extends StatelessWidget {
  const _FitBanner({required this.fit, required this.need, required this.have});

  final Fit fit;
  final String need;
  final String have;

  @override
  Widget build(BuildContext context) {
    final good = fit == Fit.match;
    final c = good ? AppColors.good : AppColors.warn;
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(color: c.withValues(alpha: 0.10), borderRadius: BorderRadius.circular(12)),
      child: Row(children: [
        Icon(good ? Icons.check_circle : Icons.info_outline, color: c),
        const SizedBox(width: 10),
        Expanded(
          child: Text(
            good ? 'هالوظيفة على قد شهادتك ($have)' : 'بتطلب $need، وإنت معك $have. فيك تقدّم إذا عندك خبرة قوية.',
            style: TextStyle(color: c, fontWeight: FontWeight.w600),
          ),
        ),
      ]),
    );
  }
}

class _Bullets extends StatelessWidget {
  const _Bullets({required this.title, required this.items});

  final String title;
  final List<String> items;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(top: 20),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(title, style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          for (final i in items)
            Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                const Padding(padding: EdgeInsets.only(top: 8), child: Icon(Icons.circle, size: 6)),
                const SizedBox(width: 10),
                Expanded(child: Text(i, style: const TextStyle(height: 1.6))),
              ]),
            ),
        ]),
      );
}

/// WhatsApp with a ready message, call, email with a ready letter, or the ad's own link.
class _ApplySection extends StatelessWidget {
  const _ApplySection({required this.job});

  final Job job;

  Future<void> _go(BuildContext context, Uri uri, String method) async {
    final s = context.read<Session>();
    s.repo.countApply(method, null);
    final ok = await launchUrl(uri, mode: LaunchMode.externalApplication);
    if (!ok && context.mounted) toast(context, 'ما قدرنا نفتح الرابط', error: true);
    if (ok && s.loggedIn && s.isSaved(job.id) == false) {
      try {
        await s.repo.setStage(job.id, 'applied');
        s.refreshSaved();
      } catch (_) {}
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final s = context.watch<Session>();
    final msgs = ApplyMessages(job, s.user, educationLabel: s.user == null ? null : s.config?.educationLabel(s.user!.profile.education));
    final wa = msgs.whatsappUri();
    final email = msgs.emailUri();
    final site = msgs.siteUri();
    final calls = job.phones.take(2).toList();
    final nothing = wa == null && email == null && site == null && calls.isEmpty;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Text('كيف بقدّم؟', style: t.textTheme.titleMedium),
      const SizedBox(height: 10),
      if (nothing)
        Text('ما في طريقة تقديم واضحة بالإعلان. شوف المصدر تحت.', style: TextStyle(color: t.colorScheme.onSurfaceVariant)),
      if (wa != null) ...[
        FilledButton.icon(
          style: FilledButton.styleFrom(backgroundColor: AppColors.whatsapp, foregroundColor: Colors.white),
          icon: const Icon(Icons.chat),
          label: const Text('قدّم عالواتساب (الرسالة جاهزة)'),
          onPressed: () => _go(context, wa, 'whatsapp'),
        ),
        const SizedBox(height: 8),
      ],
      if (email != null) ...[
        FilledButton.tonalIcon(
          style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(50)),
          icon: const Icon(Icons.email_outlined),
          label: Text('ابعت إيميل: ${job.email}', overflow: TextOverflow.ellipsis),
          onPressed: () => _go(context, email, 'email'),
        ),
        const SizedBox(height: 8),
      ],
      if (site != null) ...[
        OutlinedButton.icon(
          icon: const Icon(Icons.open_in_new),
          label: const Text('قدّم من رابط الإعلان'),
          onPressed: () => _go(context, site, 'site'),
        ),
        if (job.requiresAccount)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text('هالموقع بيطلب تعمل حساب عنده لتقدّم.', style: t.textTheme.bodySmall),
          ),
        const SizedBox(height: 8),
      ],
      for (final p in calls) ...[
        OutlinedButton.icon(
          icon: const Icon(Icons.call_outlined),
          label: Text('اتصل: $p', textDirection: TextDirection.ltr),
          onPressed: () => _go(context, Uri(scheme: 'tel', path: p.replaceAll(RegExp(r'[^\d+]'), '')), 'call'),
        ),
        const SizedBox(height: 8),
      ],
      if (wa != null || email != null)
        TextButton.icon(
          icon: const Icon(Icons.copy, size: 18),
          label: const Text('انسخ رسالة التقديم'),
          onPressed: () async {
            await Clipboard.setData(ClipboardData(text: wa != null ? msgs.whatsapp : msgs.emailBody));
            if (context.mounted) toast(context, 'انسخت ✓');
          },
        ),
      if (!s.loggedIn)
        TextButton(
          onPressed: () => context.push('/register'),
          child: const Text('اعمل حساب لتنكتب الرسالة باسمك وتتابع تقديماتك'),
        ),
    ]);
  }
}

/// Where the person is with this job: saved → applied → interview → offer / no.
class _StagePicker extends StatefulWidget {
  const _StagePicker({required this.jobId});

  final int jobId;

  @override
  State<_StagePicker> createState() => _StagePickerState();
}

class _StagePickerState extends State<_StagePicker> {
  String? _stage;

  @override
  void initState() {
    super.initState();
    context.read<Session>().repo.saved().then((items) {
      final it = items.where((i) => i.jobId == widget.jobId).firstOrNull;
      if (mounted && it != null) setState(() => _stage = it.stage);
    }).catchError((_) {});
  }

  @override
  Widget build(BuildContext context) {
    final s = context.watch<Session>();
    final stages = s.config?.stages ?? const <Option>[];
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('وين وصلت بهالوظيفة؟', style: Theme.of(context).textTheme.titleSmall),
          const SizedBox(height: 10),
          Wrap(spacing: 8, runSpacing: 8, children: [
            for (final o in stages)
              ChoiceChip(
                label: Text(o.label),
                selected: _stage == o.code,
                onSelected: (_) async {
                  final old = _stage;
                  setState(() => _stage = o.code);
                  try {
                    await s.repo.setStage(widget.jobId, o.code);
                  } catch (e) {
                    if (!context.mounted) return;
                    setState(() => _stage = old);
                    toast(context, errorText(e), error: true);
                  }
                },
              ),
          ]),
        ]),
      ),
    );
  }
}
