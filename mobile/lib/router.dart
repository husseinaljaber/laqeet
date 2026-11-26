import 'package:go_router/go_router.dart';
import 'package:material_ui/material_ui.dart';

import 'models/models.dart';
import 'screens/account/account_screen.dart';
import 'screens/account/account_forms.dart';
import 'screens/account/alerts_screen.dart';
import 'screens/auth/forgot_screen.dart';
import 'screens/auth/login_screen.dart';
import 'screens/auth/register_screen.dart';
import 'screens/auth/verify_screen.dart';
import 'screens/auth/welcome_screen.dart';
import 'screens/home_shell.dart';
import 'screens/job_details_screen.dart';
import 'screens/jobs_screen.dart';
import 'screens/profile_setup_screen.dart';
import 'screens/saved_screen.dart';
import 'screens/splash_screen.dart';
import 'state/session.dart';

const _authRoutes = {'/welcome', '/login', '/register', '/forgot'};
const _gateRoutes = {'/splash', '/verify', '/setup'};

/// Where a person may be, given where they are in sign-up. Pure, so it is tested on its own.
String? redirectFor(SessionStatus status, {required bool loggedIn, required String location}) {
  switch (status) {
    case SessionStatus.loading:
      return location == '/splash' ? null : '/splash';
    case SessionStatus.guest:
      return _authRoutes.contains(location) || location.startsWith('/job/') || location.startsWith('/j/') ? null : '/welcome';
    case SessionStatus.needsVerification:
      return location == '/verify' ? null : '/verify';
    case SessionStatus.needsProfile:
      return location == '/setup' ? null : '/setup';
    case SessionStatus.ready:
      if (_gateRoutes.contains(location) || location == '/welcome') return '/jobs';
      if (loggedIn && _authRoutes.contains(location)) return '/jobs';
      if (!loggedIn && location.startsWith('/account/')) return '/account';
      return null;
  }
}

/// A job link opened while the app was still starting: keep it and go there once ready, instead of losing it to
/// the splash screen.
class _PendingLink {
  String? location;
}

GoRouter buildRouter(Session session) {
  final pending = _PendingLink();
  return GoRouter(
      initialLocation: '/splash',
      refreshListenable: session,
      redirect: (context, state) {
        final loc = state.matchedLocation;
        final isJobLink = loc.startsWith('/j/') || loc.startsWith('/job/');
        if (session.status == SessionStatus.loading && isJobLink) pending.location = loc;
        if (loc == '/splash' &&
            pending.location != null &&
            (session.status == SessionStatus.ready || session.status == SessionStatus.guest)) {
          final go = pending.location;
          pending.location = null;
          return go;
        }
        return redirectFor(session.status, loggedIn: session.loggedIn, location: loc);
      },
      routes: [
        // The website's share links (/j/123) land on the job screen.
        GoRoute(path: '/j/:id', redirect: (_, s) => '/job/${s.pathParameters['id']}'),
        GoRoute(path: '/splash', builder: (_, _) => const SplashScreen()),
        GoRoute(path: '/welcome', builder: (_, _) => const WelcomeScreen()),
        GoRoute(path: '/login', builder: (_, _) => const LoginScreen()),
        GoRoute(path: '/register', builder: (_, _) => const RegisterScreen()),
        GoRoute(path: '/forgot', builder: (_, s) => ForgotScreen(email: s.uri.queryParameters['email'])),
        GoRoute(path: '/verify', builder: (_, _) => const VerifyScreen()),
        GoRoute(path: '/setup', builder: (_, _) => const ProfileSetupScreen()),
        GoRoute(
          path: '/job/:id',
          builder: (_, s) => JobDetailsScreen(
            id: int.tryParse(s.pathParameters['id'] ?? '') ?? 0,
            initial: s.extra is Job ? s.extra as Job : null,
          ),
        ),
        GoRoute(path: '/account/profile', builder: (_, _) => const ProfileSetupScreen(editing: true)),
        GoRoute(path: '/account/edit', builder: (_, _) => const EditNameScreen()),
        GoRoute(path: '/account/alerts', builder: (_, _) => const AlertsScreen()),
        GoRoute(path: '/account/password', builder: (_, _) => const ChangePasswordScreen()),
        GoRoute(path: '/account/delete', builder: (_, _) => const DeleteAccountScreen()),
        StatefulShellRoute.indexedStack(
          builder: (_, _, shell) => HomeShell(shell: shell),
          branches: [
            StatefulShellBranch(routes: [GoRoute(path: '/jobs', builder: (_, _) => const JobsScreen())]),
            StatefulShellBranch(routes: [GoRoute(path: '/saved', builder: (_, _) => const SavedScreen())]),
            StatefulShellBranch(routes: [GoRoute(path: '/account', builder: (_, _) => const AccountScreen())]),
          ],
        ),
      ],
      errorBuilder: (_, _) => const Scaffold(body: Center(child: Text('هالصفحة مو موجودة'))),
    );
}
