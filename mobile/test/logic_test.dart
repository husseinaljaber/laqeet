import 'package:flutter_test/flutter_test.dart';
import 'package:laqeet/core/api_client.dart';
import 'package:laqeet/core/apply.dart';
import 'package:laqeet/core/format.dart';
import 'package:laqeet/core/repository.dart';
import 'package:laqeet/core/token_store.dart';
import 'package:laqeet/models/models.dart';
import 'package:laqeet/router.dart';
import 'package:laqeet/state/jobs_controller.dart';
import 'package:laqeet/state/session.dart';

import 'fake_server.dart';

void main() {
  group('format', () {
    final now = DateTime.utc(2026, 11, 26, 12);
    test('ago', () {
      expect(ago('2026-11-26T11:30:00Z', now: now), 'هلق');
      expect(ago('2026-11-26T09:00:00Z', now: now), 'من 3 ساعات');
      expect(ago('2026-11-26T00:00:00Z', now: now), 'من 12 ساعة');
      expect(ago('2026-11-25T09:00:00Z', now: now), 'مبارح');
      expect(ago(null), '');
      expect(ago('garbage'), '');
    });
    test('deadline', () {
      final d = DateTime(2026, 11, 26);
      expect(deadlineText('2026-11-26', now: d), 'آخر يوم اليوم');
      expect(deadlineText('2026-11-27', now: d), 'آخر يوم بكرا');
      expect(deadlineText('2026-11-30', now: d), 'باقي 4 أيام');
      expect(deadlineText('2026-11-20', now: d), 'انتهى التقديم');
      expect(deadlineSoon('2026-11-28', now: d), isTrue);
      expect(deadlineSoon('2026-12-28', now: d), isFalse);
    });
    test('phones', () {
      expect(waNumber('0933 123 456', 'sy'), '963933123456');
      expect(waNumber('+963933123456', 'sy'), '963933123456');
      expect(waNumber('05321234567', 'tr'), '905321234567');
      expect(isMobile('0933123456'), isTrue);
      expect(isMobile('011 2223344'), isFalse);
      expect(isMobile('05321234567'), isTrue);
    });
    test('counts', () {
      expect(jobsCount(0), 'ما في وظائف');
      expect(jobsCount(2), 'وظيفتين');
      expect(jobsCount(5), '5 وظائف');
      expect(jobsCount(40), '40 وظيفة');
    });
  });

  group('router redirects', () {
    String? r(SessionStatus s, String loc, {bool loggedIn = false}) => redirectFor(s, loggedIn: loggedIn, location: loc);
    test('each sign-up stage has one place', () {
      expect(r(SessionStatus.loading, '/jobs'), '/splash');
      expect(r(SessionStatus.guest, '/jobs'), '/welcome');
      expect(r(SessionStatus.guest, '/login'), isNull);
      expect(r(SessionStatus.guest, '/job/5'), isNull);
      expect(r(SessionStatus.needsVerification, '/jobs', loggedIn: true), '/verify');
      expect(r(SessionStatus.needsProfile, '/verify', loggedIn: true), '/setup');
      expect(r(SessionStatus.ready, '/setup', loggedIn: true), '/jobs');
      expect(r(SessionStatus.ready, '/login', loggedIn: true), '/jobs');
      expect(r(SessionStatus.ready, '/account/profile', loggedIn: true), isNull);
    });
    test('guests browsing can still go sign in, but not into account pages', () {
      expect(r(SessionStatus.ready, '/login'), isNull);
      expect(r(SessionStatus.ready, '/account/password'), '/account');
      expect(r(SessionStatus.ready, '/saved'), isNull);
    });
  });

  group('models', () {
    test('job parses and tolerates missing fields', () {
      final j = Job.fromJson({'id': 3, 'title': '  سائق ', 'employer': ' ', 'phones': ['0933123456'], 'kind': 'quick'});
      expect(j.title, 'سائق');
      expect(j.employer, isNull);
      expect(j.hasWhatsApp, isTrue);
      expect(Job.fromJson({'id': '7'}).id, 7);
    });
    test('config labels and ranks', () {
      final c = AppConfig.fromJson(FakeServer().config);
      expect(c.educationLabel('bachelor'), 'جامعة');
      expect(c.educationLabel(null), 'غير مذكورة');
      expect(c.educationRank('secondary') < c.educationRank('bachelor'), isTrue);
      expect(c.citiesByCountry['tr'], ['إسطنبول']);
    });
  });

  test('apply message uses the person and the job', () {
    final job = Job.fromJson(FakeServer.job(1)..['email'] = 'hr@x.sy');
    const user = User(id: 1, email: 'a@b.co', firstName: 'سارة', lastName: 'علي', gender: 'female', emailVerified: true,
        profile: Profile(education: 'bachelor', experienceYears: 2));
    final m = ApplyMessages(job, user, educationLabel: 'جامعة');
    expect(m.whatsapp, contains('اسمي سارة علي'));
    expect(m.whatsapp, contains('محاسب 1'));
    expect(m.whatsapp, contains('شهادة جامعة و2 سنوات خبرة'));
    expect(m.whatsappUri().toString(), startsWith('https://wa.me/963933123456?text='));
    expect(m.emailUri()!.scheme, 'mailto');
    expect(ApplyMessages(job, null).whatsapp, isNot(contains('اسمي')));
  });

  group('api + session', () {
    late FakeServer server;
    late MemoryTokenStore tokens;
    late Session session;
    setUp(() {
      server = FakeServer();
      tokens = MemoryTokenStore();
      session = Session(repo: Repository(ApiClient(tokens: tokens, httpClient: server.client, baseUrl: 'http://x')), tokens: tokens);
    });

    test('register → verify → profile → ready', () async {
      await session.start();
      expect(session.status, SessionStatus.guest);
      await session.register(firstName: 'أحمد', lastName: 'خالد', gender: 'male', email: 'a@b.co', password: 'Sham-2026x', acceptedTerms: true);
      expect(session.status, SessionStatus.needsVerification);
      expect(tokens.refresh, isNotNull);
      await expectLater(session.verify('000000'), throwsA(isA<ApiException>().having((e) => e.message, 'message', 'الكود غلط')));
      await session.verify('123456');
      expect(session.status, SessionStatus.needsProfile);
      await session.saveProfile(const Profile(education: 'secondary', cities: ['دمشق']));
      expect(session.status, SessionStatus.ready);
      expect(session.user!.profile.cities, ['دمشق']);
    });

    test('an expired access token is renewed once, transparently', () async {
      await session.start();
      await session.register(firstName: 'أحمد', lastName: 'خالد', gender: 'male', email: 'a@b.co', password: 'Sham-2026x', acceptedTerms: true);
      server.expireNextAccess = true;
      final me = await session.repo.me();
      expect(me.email, 'a@b.co');
      expect(server.refreshCalls, 1);
      expect(tokens.access, 'a${server.accessVersion}');
    });

    test('a failed renewal signs out', () async {
      await session.start();
      await session.register(firstName: 'أحمد', lastName: 'خالد', gender: 'male', email: 'a@b.co', password: 'Sham-2026x', acceptedTerms: true);
      tokens.refresh = 'stolen-and-rotated';
      server.expireNextAccess = true;
      await expectLater(session.repo.me(), throwsA(isA<ApiException>()));
      expect(session.loggedIn, isFalse);
      expect(session.status, SessionStatus.guest);
      expect(tokens.refresh, isNull);
    });

    test('stored login is restored on start', () async {
      await session.start();
      await session.register(firstName: 'أحمد', lastName: 'خالد', gender: 'male', email: 'a@b.co', password: 'Sham-2026x', acceptedTerms: true);
      final again = Session(repo: Repository(ApiClient(tokens: tokens, httpClient: server.client, baseUrl: 'http://x')), tokens: tokens);
      await again.start();
      expect(again.user?.email, 'a@b.co');
    });

    test('server errors come back in Arabic with the field', () async {
      await session.start();
      await session.register(firstName: 'أحمد', lastName: 'خالد', gender: 'male', email: 'a@b.co', password: 'Sham-2026x', acceptedTerms: true);
      await session.logout();
      await expectLater(
        session.register(firstName: 'x', lastName: 'y', gender: 'male', email: 'a@b.co', password: 'Sham-2026x', acceptedTerms: true),
        throwsA(isA<ApiException>().having((e) => e.field, 'field', 'email').having((e) => e.status, 'status', 409)),
      );
      await expectLater(session.login('a@b.co', 'wrong-pass'), throwsA(isA<ApiException>()));
      await session.login('a@b.co', 'Sham-2026x');
      expect(session.loggedIn, isTrue);
    });

    test('saving is optimistic and synced', () async {
      await session.start();
      await session.register(firstName: 'أحمد', lastName: 'خالد', gender: 'male', email: 'a@b.co', password: 'Sham-2026x', acceptedTerms: true);
      await session.toggleSaved(9);
      expect(session.isSaved(9), isTrue);
      expect(server.saved[9], 'saved');
      await session.toggleSaved(9);
      expect(server.saved.containsKey(9), isFalse);
    });

    test('jobs controller pages to the end without duplicates', () async {
      final c = JobsController(session.repo);
      await c.reload();
      expect(c.jobs.length, 20);
      expect(c.hasMore, isTrue);
      await c.loadMore();
      await c.loadMore();
      expect(c.jobs.length, 45);
      expect(c.jobs.map((j) => j.id).toSet().length, 45);
      expect(c.hasMore, isFalse);
      await c.setQuery('لاشي');
      expect(c.jobs, isEmpty);
    });

    test('search sends smart=1 and keeps what was understood', () async {
      final c = JobsController(session.repo);
      await c.setQuery('محاسب بدمشق دوام جزئي');
      expect(server.lastJobsQuery['smart'], '1');
      expect(c.understood.map((u) => u.label), ['دمشق', 'دوام جزئي']);
      expect(c.withoutSpan('بدمشق'), 'محاسب دوام جزئي');
      await c.setQuery('محسب');
      expect(c.corrected, 'محاسب');
      await c.setQuery('');
      expect(server.lastJobsQuery.containsKey('smart'), isFalse);
      expect(c.understood, isEmpty);
    });

    test('no network gives a clear Arabic message', () async {
      final broken = ApiClient(tokens: tokens, baseUrl: 'http://127.0.0.1:1');
      await expectLater(broken.get('/api/v1/config'),
          throwsA(isA<ApiException>().having((e) => e.isNetwork, 'isNetwork', isTrue)));
    });
  });
}
