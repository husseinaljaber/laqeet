/// Data shapes, read from the server's JSON. The server validates everything; these only read.
library;

List<String> _strings(dynamic v) => v is List ? v.whereType<String>().toList() : const [];
int? _int(dynamic v) => v is int ? v : (v is num ? v.toInt() : int.tryParse('${v ?? ''}'));

class Profile {
  const Profile({
    this.educations = const [],
    this.fields = const [],
    this.country = 'sy',
    this.cities = const [],
    this.experienceYears,
    this.birthYear,
    this.complete = false,
  });

  /// Every degree the person picked (several allowed, none required), lowest first.
  final List<String> educations;
  final List<String> fields;
  final String country;
  final List<String> cities;
  final int? experienceYears;
  final int? birthYear;

  /// They went through the questions (even skipping them), so the app doesn't ask again.
  final bool complete;

  /// The highest degree picked, if any.
  String? get education => educations.isEmpty ? null : educations.last;

  factory Profile.fromJson(Map<String, dynamic>? j) {
    if (j == null) return const Profile();
    final edus = _strings(j['educations']);
    final fields = _strings(j['fields']);
    return Profile(
      educations: edus.isNotEmpty ? edus : [if (j['education'] is String) j['education'] as String],
      fields: fields.isNotEmpty ? fields : [if (j['field'] is String) j['field'] as String],
      country: (j['country'] as String?) ?? 'sy',
      cities: _strings(j['cities']),
      experienceYears: _int(j['experience_years']),
      birthYear: _int(j['birth_year']),
      complete: j['complete'] == true,
    );
  }

  Map<String, dynamic> toJson() => {
        'educations': educations,
        'fields': fields,
        'country': country,
        'cities': cities,
        'experience_years': experienceYears,
        'birth_year': birthYear,
      };

  Profile copyWith({List<String>? educations, List<String>? fields, String? country, List<String>? cities,
          int? experienceYears, bool clearExperience = false, int? birthYear, bool clearBirthYear = false}) =>
      Profile(
        educations: educations ?? this.educations,
        fields: fields ?? this.fields,
        country: country ?? this.country,
        cities: cities ?? this.cities,
        experienceYears: clearExperience ? null : (experienceYears ?? this.experienceYears),
        birthYear: clearBirthYear ? null : (birthYear ?? this.birthYear),
        complete: complete,
      );
}

class User {
  const User({
    required this.id,
    required this.email,
    required this.firstName,
    required this.lastName,
    required this.gender,
    required this.emailVerified,
    required this.profile,
  });

  final int id;
  final String email;
  final String firstName;
  final String lastName;
  final String gender;
  final bool emailVerified;
  final Profile profile;

  String get fullName => '$firstName $lastName';

  factory User.fromJson(Map<String, dynamic> j) => User(
        id: _int(j['id']) ?? 0,
        email: j['email'] as String? ?? '',
        firstName: j['first_name'] as String? ?? '',
        lastName: j['last_name'] as String? ?? '',
        gender: j['gender'] as String? ?? 'male',
        emailVerified: j['email_verified'] == true,
        profile: Profile.fromJson(j['profile'] as Map<String, dynamic>?),
      );
}

class JobSource {
  const JobSource({required this.label, required this.url});

  final String label;
  final String url;
}

class Job {
  const Job({
    required this.id,
    required this.title,
    this.employer,
    this.cities = const [],
    this.education,
    this.field,
    this.experience,
    this.deadline,
    this.postedAt,
    this.kind = 'formal',
    this.applyUrl,
    this.phones = const [],
    this.whatsapp = false,
    this.email,
    this.sector = 'private',
    this.country = 'sy',
    this.salary,
    this.shift,
    this.summary,
    this.tasks = const [],
    this.skills = const [],
    this.gender,
    this.ageMin,
    this.ageMax,
    this.official = false,
    this.sources = const [],
    this.requiresAccount = false,
    this.expiresOn,
  });

  final int id;
  final String title;
  final String? employer;
  final List<String> cities;
  final String? education;
  final String? field;
  final int? experience;
  final String? deadline;
  final String? postedAt;
  final String kind;
  final String? applyUrl;
  final List<String> phones;
  final bool whatsapp;
  final String? email;
  final String sector;
  final String country;
  final String? salary;
  final String? shift;
  final String? summary;
  final List<String> tasks;
  final List<String> skills;
  final String? gender;
  final int? ageMin;
  final int? ageMax;
  final bool official;
  final List<JobSource> sources;
  final bool requiresAccount;
  final String? expiresOn; // when the ad leaves Laqeet if no deadline is given

  bool get hasWhatsApp => phones.isNotEmpty && (whatsapp || kind == 'quick');

  factory Job.fromJson(Map<String, dynamic> j) {
    final emp = (j['employer'] as String?)?.trim();
    return Job(
      id: _int(j['id']) ?? 0,
      title: (j['title'] as String? ?? '').trim(),
      employer: (emp == null || emp.isEmpty) ? null : emp,
      cities: _strings(j['cities']),
      education: j['education'] as String?,
      field: j['field'] as String?,
      experience: _int(j['experience']),
      deadline: j['deadline'] as String?,
      postedAt: j['posted_at'] as String?,
      kind: (j['kind'] as String?) ?? 'formal',
      applyUrl: j['apply_url'] as String?,
      phones: _strings(j['phones']),
      whatsapp: j['whatsapp'] == true,
      email: j['email'] as String?,
      sector: (j['sector'] as String?) ?? 'private',
      country: (j['country'] as String?) ?? 'sy',
      salary: j['salary'] as String?,
      shift: j['shift'] as String?,
      summary: j['summary'] as String?,
      tasks: _strings(j['tasks']),
      skills: _strings(j['skills']),
      gender: j['gender'] as String?,
      ageMin: _int(j['age_min']),
      ageMax: _int(j['age_max']),
      official: j['official'] == true,
      sources: (j['sources'] is List ? j['sources'] as List : const [])
          .whereType<Map>()
          .map((s) => JobSource(label: '${s['label'] ?? ''}', url: '${s['post_url'] ?? ''}'))
          .toList(),
      requiresAccount: j['requires_account'] == true,
      expiresOn: j['expires_on'] as String?,
    );
  }
}

/// One thing the search understood from the typed words («بدمشق» → city دمشق). [span] is the words it came from.
class Understood {
  const Understood({required this.kind, required this.label, required this.span});

  final String kind;
  final String label;
  final String span;
}

class JobPage {
  const JobPage({required this.total, required this.jobs, this.understood = const [], this.corrected});

  final int total;
  final List<Job> jobs;
  final List<Understood> understood;
  final String? corrected; // the query had a typo; these are the results for this spelling

  factory JobPage.fromJson(Map<String, dynamic> j) => JobPage(
        total: _int(j['total']) ?? 0,
        jobs: (j['jobs'] as List? ?? const []).whereType<Map<String, dynamic>>().map(Job.fromJson).toList(),
        understood: (j['understood'] as List? ?? const [])
            .whereType<Map>()
            .map((c) => Understood(kind: '${c['kind']}', label: '${c['label']}', span: '${c['span'] ?? ''}'))
            .toList(),
        corrected: j['corrected'] as String?,
      );
}

class SavedItem {
  const SavedItem({required this.jobId, required this.stage, this.note, this.job});

  final int jobId;
  final String stage;
  final String? note;
  final Job? job; // null when the ad was removed

  factory SavedItem.fromJson(Map<String, dynamic> j) => SavedItem(
        jobId: _int(j['job_id']) ?? 0,
        stage: j['stage'] as String? ?? 'saved',
        note: j['note'] as String?,
        job: j['job'] is Map<String, dynamic> ? Job.fromJson(j['job'] as Map<String, dynamic>) : null,
      );
}

class Option {
  const Option(this.code, this.label);

  final String code;
  final String label;
}

/// The lists the app shows in its pickers, served by the API so the app and server always agree.
class AppConfig {
  const AppConfig({
    required this.education,
    required this.fields,
    required this.citiesByCountry,
    required this.countries,
    required this.stages,
  });

  final List<Option> education;
  final List<Option> fields;
  final Map<String, List<String>> citiesByCountry;
  final List<Option> countries;
  final List<Option> stages;

  String educationLabel(String? code) =>
      education.firstWhere((o) => o.code == code, orElse: () => const Option('', 'غير مذكورة')).label;

  String fieldLabel(String? code) => fields.firstWhere((o) => o.code == code, orElse: () => const Option('', '')).label;

  String stageLabel(String code) => stages.firstWhere((o) => o.code == code, orElse: () => Option(code, code)).label;

  int educationRank(String? code) => education.indexWhere((o) => o.code == code);

  factory AppConfig.fromJson(Map<String, dynamic> j) {
    List<Option> opts(String k) => (j[k] as List? ?? const [])
        .whereType<Map>()
        .map((o) => Option('${o['code']}', '${o['label']}'))
        .toList();
    final countries = (j['countries'] as List? ?? const []).whereType<Map>().toList();
    return AppConfig(
      education: opts('education'),
      fields: opts('fields'),
      stages: opts('stages'),
      countries: countries.map((c) => Option('${c['code']}', '${c['label']}')).toList(),
      citiesByCountry: {for (final c in countries) '${c['code']}': _strings(c['cities'])},
    );
  }
}

/// How and when job alerts arrive.
class AlertSettings {
  const AlertSettings({
    required this.mode,
    required this.dailyHour,
    required this.hours,
    required this.telegramLinked,
    required this.telegramAvailable,
    required this.pushDevices,
  });

  final String mode; // daily | instant | off
  final int dailyHour;
  final List<int> hours;
  final bool telegramLinked;
  final bool telegramAvailable;
  final int pushDevices;

  factory AlertSettings.fromJson(Map<String, dynamic> j) => AlertSettings(
        mode: j['mode'] as String? ?? 'daily',
        dailyHour: _int(j['daily_hour']) ?? 19,
        hours: (j['hours'] as List? ?? const [8, 12, 16, 19, 21]).map(_int).whereType<int>().toList(),
        telegramLinked: j['telegram_linked'] == true,
        telegramAvailable: j['telegram_available'] == true,
        pushDevices: _int(j['push_devices']) ?? 0,
      );
}
