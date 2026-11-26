import 'package:go_router/go_router.dart';
import 'package:material_ui/material_ui.dart';
import 'package:provider/provider.dart';

import '../core/api_client.dart';
import '../models/models.dart';
import '../state/session.dart';
import '../widgets/common.dart';

/// Three questions that decide which jobs fit: education, field, where. Also used to edit them later.
class ProfileSetupScreen extends StatefulWidget {
  const ProfileSetupScreen({super.key, this.editing = false});

  final bool editing;

  @override
  State<ProfileSetupScreen> createState() => _ProfileSetupScreenState();
}

class _ProfileSetupScreenState extends State<ProfileSetupScreen> {
  late Profile _p = context.read<Session>().user?.profile ?? const Profile();
  bool _busy = false;
  bool _tried = false;
  String? _error;
  static const maxCities = 5;

  Future<void> _save() async {
    setState(() {
      _tried = true;
      _error = null;
    });
    if (_p.education == null) return;
    setState(() => _busy = true);
    try {
      await context.read<Session>().saveProfile(_p);
      if (!mounted) return;
      if (widget.editing) {
        toast(context, 'انحفظ ✓');
        context.pop();
      }
    } on ApiException catch (e) {
      setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = context.watch<Session>();
    final cfg = s.config;
    final t = Theme.of(context);
    if (cfg == null) return const Scaffold(body: Center(child: CircularProgressIndicator()));
    final cities = cfg.citiesByCountry[_p.country] ?? const [];
    return FormPage(
      title: widget.editing ? 'شهادتي ومدينتي' : null,
      children: [
        if (!widget.editing) ...[
          Text('أهلين ${s.user?.firstName ?? ''}!', style: t.textTheme.headlineSmall),
          const SizedBox(height: 6),
          Text('٣ أسئلة لنفرزلك الوظائف اللي بتناسبك. فيك تغيّرهن بأي وقت.',
              style: TextStyle(color: t.colorScheme.onSurfaceVariant)),
          const SizedBox(height: 20),
        ],
        _Section(
          number: '١',
          title: 'شو أعلى شهادة معك؟',
          error: _tried && _p.education == null ? 'اختار شهادتك' : null,
          child: Wrap(spacing: 8, runSpacing: 8, children: [
            for (final o in cfg.education)
              ChoiceChip(
                label: Text(o.label),
                selected: _p.education == o.code,
                onSelected: (_) => setState(() => _p = _p.copyWith(education: o.code)),
              ),
          ]),
        ),
        _Section(
          number: '٢',
          title: 'بأي مجال؟',
          hint: 'اختياري. إذا ما اخترت، منفرجيك كل المجالات.',
          child: Wrap(spacing: 8, runSpacing: 8, children: [
            ChoiceChip(
              label: const Text('كل المجالات'),
              selected: _p.field == null,
              onSelected: (_) => setState(() => _p = _p.copyWith(clearField: true)),
            ),
            for (final o in cfg.fields)
              ChoiceChip(
                label: Text(o.label),
                selected: _p.field == o.code,
                onSelected: (_) => setState(() => _p = _p.copyWith(field: o.code)),
              ),
          ]),
        ),
        _Section(
          number: '٣',
          title: 'وين بدك تشتغل؟',
          hint: 'اختار لحد $maxCities مدن، أو اتركها فاضية لتشوف كل المدن.',
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            SegmentedButton<String>(
              segments: [for (final c in cfg.countries) ButtonSegment(value: c.code, label: Text(c.label))],
              selected: {_p.country},
              onSelectionChanged: (v) => setState(() => _p = _p.copyWith(country: v.first, cities: const [])),
            ),
            const SizedBox(height: 12),
            Wrap(spacing: 8, runSpacing: 8, children: [
              for (final c in cities)
                FilterChip(
                  label: Text(c),
                  selected: _p.cities.contains(c),
                  onSelected: (on) {
                    if (on && _p.cities.length >= maxCities) {
                      toast(context, 'لحد $maxCities مدن بس');
                      return;
                    }
                    setState(() => _p = _p.copyWith(
                        cities: on ? [..._p.cities, c] : _p.cities.where((x) => x != c).toList()));
                  },
                ),
            ]),
          ]),
        ),
        _Section(
          number: '٤',
          title: 'كم سنة خبرة عندك؟',
          hint: 'اختياري.',
          child: Wrap(spacing: 8, runSpacing: 8, children: [
            for (final (label, years) in const [('بلا خبرة', 0), ('سنة', 1), ('سنتين', 2), ('٣–٤', 3), ('٥+', 5), ('١٠+', 10)])
              ChoiceChip(
                label: Text(label),
                selected: _p.experienceYears == years,
                onSelected: (on) => setState(() => _p = on
                    ? _p.copyWith(experienceYears: years)
                    : _p.copyWith(clearExperience: true)),
              ),
          ]),
        ),
        const SizedBox(height: 8),
        if (_error != null) ...[ErrorBox(_error!), const SizedBox(height: 12)],
        BusyButton(label: widget.editing ? 'احفظ' : 'فرجيني الوظائف', busy: _busy, onPressed: _save),
      ],
    );
  }
}

class _Section extends StatelessWidget {
  const _Section({required this.number, required this.title, required this.child, this.hint, this.error});

  final String number;
  final String title;
  final String? hint;
  final String? error;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.only(bottom: 26),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Row(children: [
          CircleAvatar(
            radius: 13,
            backgroundColor: t.colorScheme.primary,
            child: Text(number, style: TextStyle(color: t.colorScheme.onPrimary, fontSize: 13, fontWeight: FontWeight.w700)),
          ),
          const SizedBox(width: 10),
          Expanded(child: Text(title, style: t.textTheme.titleMedium)),
        ]),
        if (hint != null)
          Padding(
            padding: const EdgeInsetsDirectional.only(start: 36, top: 2),
            child: Text(hint!, style: t.textTheme.bodySmall?.copyWith(color: t.colorScheme.onSurfaceVariant)),
          ),
        const SizedBox(height: 12),
        child,
        if (error != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(error!, style: TextStyle(color: t.colorScheme.error, fontSize: 12)),
          ),
      ]),
    );
  }
}
