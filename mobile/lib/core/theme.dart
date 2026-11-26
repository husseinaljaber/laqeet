import 'package:material_ui/material_ui.dart';

/// Laqeet's look: deep green with a mint accent, Arabic type (IBM Plex Sans Arabic, Alexandria for headings).
class AppColors {
  static const green = Color(0xFF0B6B53);
  static const greenDark = Color(0xFF0B1C16);
  static const mint = Color(0xFF2DB88A);
  static const whatsapp = Color(0xFF128140);
  static const good = Color(0xFF18794E);
  static const warn = Color(0xFFB45309);
  static const bad = Color(0xFFB42318);
}

ThemeData buildTheme(Brightness brightness) {
  final dark = brightness == Brightness.dark;
  final scheme = ColorScheme.fromSeed(
    seedColor: AppColors.green,
    brightness: brightness,
    primary: dark ? AppColors.mint : AppColors.green,
    secondary: AppColors.mint,
    surface: dark ? const Color(0xFF15211C) : Colors.white,
  );
  final base = ThemeData(useMaterial3: true, colorScheme: scheme, fontFamily: 'Plex');
  final text = base.textTheme.copyWith(
    headlineMedium: base.textTheme.headlineMedium?.copyWith(fontFamily: 'Alexandria', fontWeight: FontWeight.w800),
    headlineSmall: base.textTheme.headlineSmall?.copyWith(fontFamily: 'Alexandria', fontWeight: FontWeight.w700),
    titleLarge: base.textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w700),
    titleMedium: base.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
  );
  final radius = BorderRadius.circular(14);
  return base.copyWith(
    scaffoldBackgroundColor: dark ? const Color(0xFF0E1714) : const Color(0xFFF5F7F6),
    textTheme: text,
    appBarTheme: AppBarTheme(
      centerTitle: false,
      elevation: 0,
      scrolledUnderElevation: 0.5,
      backgroundColor: dark ? const Color(0xFF0E1714) : const Color(0xFFF5F7F6),
      titleTextStyle: text.titleLarge?.copyWith(color: scheme.onSurface, fontFamily: 'Alexandria'),
    ),
    cardTheme: CardThemeData(
      elevation: 0,
      margin: EdgeInsets.zero,
      color: scheme.surface,
      shape: RoundedRectangleBorder(borderRadius: radius, side: BorderSide(color: scheme.outlineVariant)),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: dark ? const Color(0xFF1C2A24) : const Color(0xFFF0F3F1),
      border: OutlineInputBorder(borderRadius: radius, borderSide: BorderSide.none),
      enabledBorder: OutlineInputBorder(borderRadius: radius, borderSide: BorderSide(color: scheme.outlineVariant)),
      focusedBorder: OutlineInputBorder(borderRadius: radius, borderSide: BorderSide(color: scheme.primary, width: 2)),
      errorBorder: OutlineInputBorder(borderRadius: radius, borderSide: BorderSide(color: scheme.error)),
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        minimumSize: const Size.fromHeight(52),
        shape: RoundedRectangleBorder(borderRadius: radius),
        textStyle: const TextStyle(fontFamily: 'Plex', fontWeight: FontWeight.w700, fontSize: 16),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        minimumSize: const Size.fromHeight(50),
        shape: RoundedRectangleBorder(borderRadius: radius),
        textStyle: const TextStyle(fontFamily: 'Plex', fontWeight: FontWeight.w600, fontSize: 15),
      ),
    ),
    chipTheme: base.chipTheme.copyWith(shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(999))),
    navigationBarTheme: NavigationBarThemeData(
      indicatorColor: scheme.primary.withValues(alpha: 0.14),
      labelTextStyle: WidgetStateProperty.all(const TextStyle(fontFamily: 'Plex', fontWeight: FontWeight.w600, fontSize: 12)),
    ),
    snackBarTheme: const SnackBarThemeData(behavior: SnackBarBehavior.floating),
  );
}
