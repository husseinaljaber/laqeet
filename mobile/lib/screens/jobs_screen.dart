import 'dart:async';

import 'package:go_router/go_router.dart';
import 'package:material_ui/material_ui.dart';
import 'package:provider/provider.dart';

import '../core/format.dart';
import '../models/models.dart';
import '../state/jobs_controller.dart';
import '../state/session.dart';
import '../widgets/common.dart';
import '../widgets/job_card.dart';

class JobsScreen extends StatefulWidget {
  const JobsScreen({super.key});

  @override
  State<JobsScreen> createState() => _JobsScreenState();
}

class _JobsScreenState extends State<JobsScreen> {
  late final Session _session = context.read<Session>();
  late final JobsController _c = JobsController(_session.repo);
  final _search = TextEditingController();
  final _scroll = ScrollController();
  Timer? _debounce;
  bool _forMe = true;
  Profile? _profile;

  bool get _canPersonalize => _session.user?.profile.complete ?? false;

  @override
  void initState() {
    super.initState();
    _scroll.addListener(() {
      if (_scroll.position.pixels > _scroll.position.maxScrollExtent - 600) _c.loadMore();
    });
    _session.addListener(_onSession);
    _profile = _session.user?.profile;
    _c.setFilters(_baseFilters());
  }

  JobFilters _baseFilters() {
    final p = _session.user?.profile;
    if (p == null || !p.complete) return const JobFilters();
    return _forMe ? JobFilters.fromProfile(p) : JobFilters(country: p.country);
  }

  /// The profile was edited (or the person logged in/out): refresh the list.
  void _onSession() {
    final p = _session.user?.profile;
    if (identical(p, _profile)) return;
    _profile = p;
    _c.setFilters(_baseFilters());
  }

  @override
  void dispose() {
    _session.removeListener(_onSession);
    _debounce?.cancel();
    _search.dispose();
    _scroll.dispose();
    _c.dispose();
    super.dispose();
  }

  void _onSearch(String v) {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 450), () => _c.setQuery(v));
  }

  void _setMode(bool forMe) {
    if (forMe == _forMe) return;
    setState(() => _forMe = forMe);
    _c.setFilters(_baseFilters());
  }

  Future<void> _openFilters() async {
    final f = await showModalBottomSheet<JobFilters>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) => FilterSheet(initial: _c.filters, config: _session.config!),
    );
    if (f == null) return;
    setState(() => _forMe = false);
    _c.setFilters(f);
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: const Logo(size: 30), toolbarHeight: 60),
      body: ListenableBuilder(
        listenable: _c,
        builder: (context, _) => RefreshIndicator(
          onRefresh: _c.reload,
          child: CustomScrollView(
            controller: _scroll,
            physics: const AlwaysScrollableScrollPhysics(),
            slivers: [
              SliverToBoxAdapter(
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(16, 4, 16, 8),
                  child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                    TextField(
                      controller: _search,
                      onChanged: _onSearch,
                      onSubmitted: _c.setQuery,
                      textInputAction: TextInputAction.search,
                      decoration: InputDecoration(
                        hintText: 'احكي متل ما بدك: محاسب بدمشق دوام جزئي',
                        prefixIcon: const Icon(Icons.search),
                        suffixIcon: _search.text.isEmpty
                            ? null
                            : IconButton(
                                tooltip: 'امسح البحث',
                                icon: const Icon(Icons.close),
                                onPressed: () {
                                  _search.clear();
                                  _c.setQuery('');
                                },
                              ),
                      ),
                    ),
                    const SizedBox(height: 10),
                    SingleChildScrollView(
                      scrollDirection: Axis.horizontal,
                      child: Row(children: [
                        if (_canPersonalize) ...[
                          ChoiceChip(
                            avatar: const Icon(Icons.auto_awesome, size: 16),
                            label: const Text('مناسبة إلك'),
                            selected: _forMe,
                            onSelected: (_) => _setMode(true),
                          ),
                          const SizedBox(width: 8),
                          ChoiceChip(label: const Text('كل الوظائف'), selected: !_forMe, onSelected: (_) => _setMode(false)),
                          const SizedBox(width: 8),
                        ],
                        ActionChip(
                          avatar: const Icon(Icons.tune, size: 18),
                          label: Text(!_forMe && _c.filters.activeCount > 0 ? 'فلترة (${_c.filters.activeCount})' : 'فلترة'),
                          onPressed: _session.config == null ? null : _openFilters,
                        ),
                      ]),
                    ),
                    if (_c.understood.isNotEmpty) ...[
                      const SizedBox(height: 8),
                      Semantics(
                        container: true,
                        label: 'فهمنا من بحثك',
                        child: Wrap(spacing: 6, runSpacing: 6, children: [
                          for (final u in _c.understood)
                            InputChip(
                              avatar: Icon(_kindIcon(u.kind), size: 16),
                              label: Text(u.label),
                              visualDensity: VisualDensity.compact,
                              deleteButtonTooltipMessage: 'شيل «${u.label}»',
                              onDeleted: u.span.isEmpty
                                  ? null
                                  : () {
                                      final q = _c.withoutSpan(u.span);
                                      _search.text = q;
                                      _c.setQuery(q);
                                    },
                            ),
                        ]),
                      ),
                    ],
                    if (_c.corrected != null && !_c.loading) ...[
                      const SizedBox(height: 8),
                      Text.rich(TextSpan(children: [
                        const TextSpan(text: 'ما لقينا شي بالكتابة هي، فرجيناك نتائج '),
                        TextSpan(text: '«${_c.corrected}»', style: const TextStyle(fontWeight: FontWeight.w700)),
                      ]), style: t.textTheme.bodySmall),
                    ],
                    const SizedBox(height: 8),
                    if (!_c.loading && _c.error == null)
                      Text(
                        _forMe && _canPersonalize
                            ? '${jobsCount(_c.total)} على قد شهادتك${_profile!.cities.isEmpty ? '' : ' بمدنك'}'
                            : jobsCount(_c.total),
                        style: t.textTheme.bodySmall?.copyWith(color: t.colorScheme.onSurfaceVariant),
                      ),
                  ]),
                ),
              ),
              ..._body(context),
            ],
          ),
        ),
      ),
    );
  }

  List<Widget> _body(BuildContext context) {
    if (_c.loading && _c.jobs.isEmpty) {
      return [
        SliverList.separated(
          itemCount: 5,
          separatorBuilder: (_, _) => const SizedBox(height: 10),
          itemBuilder: (_, _) => const Padding(padding: EdgeInsets.symmetric(horizontal: 16), child: _SkeletonCard()),
        ),
      ];
    }
    if (_c.error != null && _c.jobs.isEmpty) {
      return [
        SliverFillRemaining(
          hasScrollBody: false,
          child: MessageView(icon: Icons.wifi_off_rounded, title: 'ما قدرنا نجيب الوظائف', body: _c.error, action: 'جرّب كمان مرة', onAction: _c.reload),
        ),
      ];
    }
    if (_c.jobs.isEmpty) {
      return [
        SliverFillRemaining(
          hasScrollBody: false,
          child: MessageView(
            icon: Icons.search_off_rounded,
            title: 'ما لقينا وظائف هون',
            body: _forMe && _canPersonalize
                ? 'جرّب «كل الوظائف»، أو زيد مدن بملفك.'
                : 'جرّب كلمة تانية أو خفّف الفلترة.',
            action: _forMe && _canPersonalize ? 'فرجيني كل الوظائف' : null,
            onAction: () => _setMode(false),
          ),
        ),
      ];
    }
    return [
      SliverPadding(
        padding: const EdgeInsets.symmetric(horizontal: 16),
        sliver: SliverList.separated(
          itemCount: _c.jobs.length,
          separatorBuilder: (_, _) => const SizedBox(height: 10),
          itemBuilder: (context, i) {
            final job = _c.jobs[i];
            return JobCard(job: job, onTap: () => context.push('/job/${job.id}', extra: job));
          },
        ),
      ),
      SliverToBoxAdapter(
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Center(
            child: _c.loadingMore
                ? const CircularProgressIndicator()
                : _c.hasMore
                    ? TextButton(onPressed: _c.loadMore, child: const Text('حمّل أكتر'))
                    : Text('هدول كل الوظائف ✓', style: Theme.of(context).textTheme.bodySmall),
          ),
        ),
      ),
    ];
  }
}

IconData _kindIcon(String kind) => switch (kind) {
      'city' || 'country' => Icons.place_outlined,
      'edu' => Icons.school_outlined,
      'shift' => Icons.schedule,
      'exp' => Icons.timeline,
      'sector' => Icons.apartment_outlined,
      'gender' => Icons.person_outline,
      'new' => Icons.fiber_new_outlined,
      _ => Icons.label_outline,
    };

class _SkeletonCard extends StatelessWidget {
  const _SkeletonCard();

  @override
  Widget build(BuildContext context) {
    final c = Theme.of(context).colorScheme.onSurface.withValues(alpha: 0.07);
    Widget bar(double w, double h) =>
        Container(width: w, height: h, decoration: BoxDecoration(color: c, borderRadius: BorderRadius.circular(6)));
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          bar(220, 16),
          const SizedBox(height: 10),
          bar(140, 12),
          const SizedBox(height: 14),
          Row(children: [bar(70, 20), const SizedBox(width: 6), bar(90, 20), const SizedBox(width: 6), bar(60, 20)]),
        ]),
      ),
    );
  }
}

/// Country, city, education, sector, field and order.
class FilterSheet extends StatefulWidget {
  const FilterSheet({super.key, required this.initial, required this.config});

  final JobFilters initial;
  final AppConfig config;

  @override
  State<FilterSheet> createState() => _FilterSheetState();
}

class _FilterSheetState extends State<FilterSheet> {
  late JobFilters _f = widget.initial.city?.contains(',') ?? false ? widget.initial.copyWith(clearCity: true) : widget.initial;

  static const _sectors = [('gov', 'حكومي'), ('ngo', 'منظمات'), ('private', 'خاص')];

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final cfg = widget.config;
    Widget title(String s) => Padding(
          padding: const EdgeInsets.only(top: 18, bottom: 8),
          child: Text(s, style: t.textTheme.titleSmall),
        );
    return DraggableScrollableSheet(
      expand: false,
      initialChildSize: 0.8,
      maxChildSize: 0.95,
      builder: (context, scroll) => Column(children: [
        Expanded(
          child: ListView(controller: scroll, padding: const EdgeInsets.fromLTRB(20, 0, 20, 12), children: [
            Text('فلترة', style: t.textTheme.titleLarge),
            title('البلد'),
            SegmentedButton<String>(
              segments: [for (final c in cfg.countries) ButtonSegment(value: c.code, label: Text(c.label))],
              selected: {_f.country},
              onSelectionChanged: (v) => setState(() => _f = _f.copyWith(country: v.first, clearCity: true)),
            ),
            title('المدينة'),
            DropdownButtonFormField<String?>(
              initialValue: _f.city,
              isExpanded: true,
              items: [
                const DropdownMenuItem(value: null, child: Text('كل المدن')),
                for (final c in cfg.citiesByCountry[_f.country] ?? const <String>[]) DropdownMenuItem(value: c, child: Text(c)),
              ],
              onChanged: (v) => setState(() => _f = v == null ? _f.copyWith(clearCity: true) : _f.copyWith(city: v)),
            ),
            title('شهادتي (منفرجيك يلي بتطلب هالشهادة أو أقل)'),
            Wrap(spacing: 8, runSpacing: 8, children: [
              ChoiceChip(label: const Text('أي شهادة'), selected: _f.education == null, onSelected: (_) => setState(() => _f = _f.copyWith(clearEducation: true))),
              for (final o in cfg.education)
                ChoiceChip(label: Text(o.label), selected: _f.education == o.code, onSelected: (_) => setState(() => _f = _f.copyWith(education: o.code))),
            ]),
            title('الجهة'),
            Wrap(spacing: 8, runSpacing: 8, children: [
              ChoiceChip(label: const Text('الكل'), selected: _f.sector == null, onSelected: (_) => setState(() => _f = _f.copyWith(clearSector: true))),
              for (final (code, label) in _sectors)
                ChoiceChip(label: Text(label), selected: _f.sector == code, onSelected: (_) => setState(() => _f = _f.copyWith(sector: code))),
            ]),
            title('المجال'),
            DropdownButtonFormField<String?>(
              initialValue: _f.field,
              isExpanded: true,
              items: [
                const DropdownMenuItem(value: null, child: Text('كل المجالات')),
                for (final o in cfg.fields) DropdownMenuItem(value: o.code, child: Text(o.label, overflow: TextOverflow.ellipsis)),
              ],
              onChanged: (v) => setState(() => _f = v == null ? _f.copyWith(clearField: true) : _f.copyWith(field: v)),
            ),
            title('الترتيب'),
            SegmentedButton<bool>(
              segments: const [
                ButtonSegment(value: false, label: Text('الأنسب')),
                ButtonSegment(value: true, label: Text('الأحدث')),
              ],
              selected: {_f.recent},
              onSelectionChanged: (v) => setState(() => _f = _f.copyWith(recent: v.first)),
            ),
          ]),
        ),
        SafeArea(
          top: false,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(20, 8, 20, 12),
            child: Row(children: [
              Expanded(
                child: OutlinedButton(
                  onPressed: () => setState(() => _f = JobFilters(country: _f.country)),
                  child: const Text('امسح الكل'),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(flex: 2, child: FilledButton(onPressed: () => Navigator.pop(context, _f), child: const Text('طبّق'))),
            ]),
          ),
        ),
      ]),
    );
  }
}
