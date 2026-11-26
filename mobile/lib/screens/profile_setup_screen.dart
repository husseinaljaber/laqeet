import 'package:go_router/go_router.dart';
import 'package:material_ui/material_ui.dart';
import 'package:provider/provider.dart';

import '../core/api_client.dart';
import '../models/models.dart';
import '../state/session.dart';
import '../widgets/common.dart';

/// What decides «مناسبة إلك»: degrees, fields, where, experience. Everything optional, several of each allowed,
/// and the whole thing can be skipped. Also used to edit them later from حسابي.
class ProfileSetupScreen extends StatefulWidget {
  const ProfileSetupScreen({super.key, this.editing = false});

  final bool editing;

  @override
  State<ProfileSetupScreen> createState() => _ProfileSetupScreenState();
}

class _ProfileSetupScreenState extends State<ProfileSetupScreen> {
  late Profile _p = context.read<Session>().user?.profile ?? const Profile();
  bool _busy = false;
  String? _error;
  static const maxCities = 10;
  static const maxFields = 8;

  List<String> _toggle(List<String> list, String v, bool on) =>
      on ? [...list, v] : list.where((x) => x != v).toList();

  Future<void> _save({bool skip = false}) async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await context.read<Session>().saveProfile(skip ? Profile(country: _p.country) : _p);
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
    final cities = cfg.citiesByCountry[_p.country] ?? const <String>[];
    final muted = TextStyle(color: t.colorScheme.onSurfaceVariant);
    return FormPage(
      title: widget.editing ? 'شهاداتي ومجالاتي ومدني' : null,
      children: [
        if (!widget.editing) ...[
          Row(children: [
            Expanded(child: Text('أهلين ${s.user?.firstName ?? ''}!', style: t.textTheme.headlineSmall)),
            TextButton(onPressed: _busy ? null : () => _save(skip: true), child: const Text('تخطّي')),
          ]),
          const SizedBox(height: 6),
          Text('اختار شو بيناسبك لنفرزلك الوظائف. فيك تختار أكتر من وحدة بكل سؤال، أو تتركه فاضي، وفيك تغيّر بأي وقت.',
              style: muted.copyWith(height: 1.6)),
          const SizedBox(height: 20),
        ],
        _Section(
          number: '١',
          title: 'شو الشهادات يلي معك؟',
          hint: _p.educations.isEmpty ? 'ما اخترت شي: منفرجيك وظائف كل الشهادات.' : 'اخترت ${_p.educations.length}.',
          child: Wrap(spacing: 8, runSpacing: 8, children: [
            for (final o in cfg.education)
              FilterChip(
                label: Text(o.label),
                selected: _p.educations.contains(o.code),
                onSelected: (on) => setState(() => _p = _p.copyWith(educations: _toggle(_p.educations, o.code, on))),
              ),
          ]),
        ),
        _Section(
          number: '٢',
          title: 'بأي مجالات بدك تشتغل؟',
          hint: _p.fields.isEmpty ? 'ما اخترت شي: منفرجيك كل المجالات.' : 'لحد $maxFields مجالات.',
          child: Wrap(spacing: 8, runSpacing: 8, children: [
            for (final o in cfg.fields)
              FilterChip(
                label: Text(o.label),
                selected: _p.fields.contains(o.code),
                onSelected: (on) {
                  if (on && _p.fields.length >= maxFields) {
                    toast(context, 'لحد $maxFields مجالات بس');
                    return;
                  }
                  setState(() => _p = _p.copyWith(fields: _toggle(_p.fields, o.code, on)));
                },
              ),
          ]),
        ),
        _Section(
          number: '٣',
          title: 'وين بدك تشتغل؟',
          hint: _p.cities.isEmpty ? 'ما اخترت مدينة: منفرجيك كل المدن.' : 'لحد $maxCities مدن.',
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
                    setState(() => _p = _p.copyWith(cities: _toggle(_p.cities, c, on)));
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
                onSelected: (on) => setState(() => _p = on ? _p.copyWith(experienceYears: years) : _p.copyWith(clearExperience: true)),
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
  const _Section({required this.number, required this.title, required this.child, this.hint});

  final String number;
  final String title;
  final String? hint;
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
      ]),
    );
  }
}
