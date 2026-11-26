import 'dart:async';

import 'package:laqeet/core/push.dart';

/// Stands in for Firebase in tests.
class FakePush extends PushService {
  FakePush({this.allow = true});

  bool allow;
  bool permitted = false;
  final refresh = StreamController<String>.broadcast();
  final opened = StreamController<String>.broadcast();
  final fg = StreamController<(String, String, String)>.broadcast();
  String token = 'fake-device-token-0123456789';

  @override
  bool get available => true;
  @override
  Future<String?> enable() async {
    permitted = allow;
    return allow ? token : null;
  }

  @override
  Future<String?> currentToken() async => permitted ? token : null;
  @override
  Stream<String> get tokenRefresh => refresh.stream;
  @override
  Stream<String> get openedRoutes => opened.stream;
  @override
  Future<String?> initialRoute() async => null;
  @override
  Stream<(String, String, String)> get foreground => fg.stream;
  @override
  String get platform => 'android';
}
