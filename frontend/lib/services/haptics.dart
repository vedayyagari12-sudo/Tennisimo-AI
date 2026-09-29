import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

/// The physical feels this app is allowed to produce.
///
/// Deliberately short. Every one of these maps onto a [HapticFeedback] call
/// that both Android and iOS actually implement; nothing here invents a
/// pattern out of timed vibrations.
enum HapticKind {
  /// A discrete "that registered" tick: a tap landed, a tab changed.
  selection,

  /// A soft confirmation.
  light,

  /// A real event completed.
  medium,

  /// The heaviest thump. Reserved for the one moment that is genuinely
  /// physical: the camera starting to roll.
  heavy,
}

/// Where a haptic actually goes.
///
/// Exists so a test can assert which feel a screen asked for without a
/// platform channel, and so the single production implementation is the only
/// place `HapticFeedback` is named.
abstract class HapticDriver {
  const HapticDriver();

  Future<void> send(HapticKind kind);
}

/// The real device implementation.
class PlatformHapticDriver implements HapticDriver {
  const PlatformHapticDriver();

  @override
  Future<void> send(HapticKind kind) {
    switch (kind) {
      case HapticKind.selection:
        return HapticFeedback.selectionClick();
      case HapticKind.light:
        return HapticFeedback.lightImpact();
      case HapticKind.medium:
        return HapticFeedback.mediumImpact();
      case HapticKind.heavy:
        return HapticFeedback.heavyImpact();
    }
  }
}

/// The app's haptic vocabulary, named by MEANING rather than by strength.
///
/// Screens call [recordingStarted], not `heavyImpact`, so the mapping from
/// event to feel lives in exactly one file and can be re-tuned (or later put
/// behind a user setting) without touching a widget.
///
/// The web gate also lives here, once. `HapticFeedback` goes over a platform
/// channel that no browser implements, so on web every call below returns
/// without touching the channel at all.
class Haptics {
  Haptics({
    HapticDriver driver = const PlatformHapticDriver(),
    bool isWeb = kIsWeb,
  })  : _driver = driver,
        _isWeb = isWeb;

  final HapticDriver _driver;
  final bool _isWeb;

  /// True when this instance will never fire. Web, always.
  bool get isSilent => _isWeb;

  /// Recording has begun — the most physical moment in the app.
  Future<void> recordingStarted() => _fire(HapticKind.heavy);

  /// Recording has ended and the clip is in hand.
  Future<void> recordingStopped() => _fire(HapticKind.medium);

  /// One calibration endpoint was placed on the frozen preview.
  Future<void> calibrationPointPlaced() => _fire(HapticKind.selection);

  /// A bottom-nav tab changed.
  Future<void> tabSelected() => _fire(HapticKind.selection);

  /// A finished analysis arrived: the payoff.
  Future<void> analysisComplete() => _fire(HapticKind.medium);

  /// Something the user physically asked for failed. Used sparingly: only
  /// where a deliberate press produced nothing.
  Future<void> actionFailed() => _fire(HapticKind.light);

  /// The user committed to permanently deleting their account. A firm feel
  /// for the one irreversible press in the app, but not [HapticKind.heavy],
  /// which stays reserved for the camera.
  Future<void> accountDeletionConfirmed() => _fire(HapticKind.medium);

  Future<void> _fire(HapticKind kind) async {
    // The one and only web gate.
    if (_isWeb) return;
    await _driver.send(kind);
  }
}

/// The instance every screen uses.
///
/// Mutable so a test can swap in a recording driver, in the same spirit as the
/// rest of this app's service layer. Production code never assigns to it.
Haptics haptics = Haptics();
