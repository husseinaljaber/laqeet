import 'package:material_ui/material_ui.dart';

import '../core/api_client.dart';
import '../core/repository.dart';
import '../models/models.dart';

/// What the job list is showing.
class JobFilters {
  const JobFilters({this.country = 'sy', this.city, this.education, this.lower = true, this.sector, this.field, this.recent = false});

  final String country;
  final String? city; // one city, or several joined by ","
  final String? education;
  final bool lower; // include jobs that ask for less than [education]
  final String? sector;
  final String? field;
  final bool recent;

  /// Filters taken from the person's profile: their level and below, their cities and field.
  factory JobFilters.fromProfile(Profile p) => JobFilters(
        country: p.country,
        city: p.cities.isEmpty ? null : p.cities.join(','),
        education: p.education,
        field: p.field,
      );

  int get activeCount => [city, education, sector, field].where((x) => x != null).length + (recent ? 1 : 0);

  JobFilters copyWith({
    String? country,
    String? city,
    bool clearCity = false,
    String? education,
    bool clearEducation = false,
    String? sector,
    bool clearSector = false,
    String? field,
    bool clearField = false,
    bool? recent,
  }) =>
      JobFilters(
        country: country ?? this.country,
        city: clearCity ? null : (city ?? this.city),
        education: clearEducation ? null : (education ?? this.education),
        lower: lower,
        sector: clearSector ? null : (sector ?? this.sector),
        field: clearField ? null : (field ?? this.field),
        recent: recent ?? this.recent,
      );
}

/// Loads pages of jobs for a query + filters, with paging, refresh and a guard against stale responses.
class JobsController extends ChangeNotifier {
  JobsController(this.repo);

  final Repository repo;
  static const pageSize = 20;

  JobFilters _filters = const JobFilters();
  String _query = '';
  final List<Job> jobs = [];
  int total = 0;
  bool loading = false;
  bool loadingMore = false;
  String? error;
  int _generation = 0;

  JobFilters get filters => _filters;
  String get query => _query;
  bool get hasMore => jobs.length < total;

  Future<void> setFilters(JobFilters f) {
    _filters = f;
    return reload();
  }

  Future<void> setQuery(String q) {
    if (q.trim() == _query) return Future.value();
    _query = q.trim();
    return reload();
  }

  Future<JobPage> _fetch(int offset) => repo.jobs(
        query: _query.isEmpty ? null : _query,
        education: _filters.education,
        lower: _filters.education != null && _filters.lower,
        city: _filters.city,
        country: _filters.country,
        sector: _filters.sector,
        field: _filters.field,
        recent: _filters.recent,
        offset: offset,
        limit: pageSize,
      );

  Future<void> reload() async {
    final gen = ++_generation;
    loading = true;
    error = null;
    notifyListeners();
    try {
      final page = await _fetch(0);
      if (gen != _generation) return;
      jobs
        ..clear()
        ..addAll(page.jobs);
      total = page.total;
    } on ApiException catch (e) {
      if (gen != _generation) return;
      error = e.message;
    } finally {
      if (gen == _generation) {
        loading = false;
        notifyListeners();
      }
    }
  }

  Future<void> loadMore() async {
    if (loading || loadingMore || !hasMore) return;
    final gen = _generation;
    loadingMore = true;
    notifyListeners();
    try {
      final page = await _fetch(jobs.length);
      if (gen != _generation) return;
      final seen = jobs.map((j) => j.id).toSet();
      jobs.addAll(page.jobs.where((j) => seen.add(j.id)));
      total = page.total;
      if (page.jobs.isEmpty) total = jobs.length;
    } on ApiException {
      // the "load more" row offers a retry
    } finally {
      loadingMore = false;
      notifyListeners();
    }
  }
}
