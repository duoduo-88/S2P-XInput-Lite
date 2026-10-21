"""Small pure runtime decisions kept testable without native Windows drivers."""


def should_freeze_gyro_output(
    activation_mode,
    button_freeze_ms,
    stabilization_changed,
):
    """Only the configured stabilization button may trigger gyro freeze."""
    return (
        str(activation_mode).strip().upper() != "OFF"
        and float(button_freeze_ms) > 0.0
        and bool(stabilization_changed)
    )
