"""Small, dependency-free helpers for runtime readiness and safe teardown."""


def controller_application_ready(controller):
    """Use transport readiness, not merely a physical-link indication."""
    return bool(getattr(controller, "is_ready", False))


def close_xinput_after_dispatcher(
    input_dispatcher,
    xinput,
    stop_timeout=1.0,
    grace_timeout=1.0,
):
    """Never mutate XInput state while a claimed input callback may use it."""
    dispatcher_stopped = True
    if input_dispatcher is not None:
        dispatcher_stopped = input_dispatcher.stop(timeout=stop_timeout)
        if not dispatcher_stopped and grace_timeout > 0:
            dispatcher_stopped = input_dispatcher.stop(
                timeout=grace_timeout
            )
    if dispatcher_stopped:
        return xinput.close() is not False

    # Stop the physical-rumble producer, but leave virtual-pad state intact:
    # the timed-out input callback may still be inside handle_input().
    stop_rumble = getattr(xinput, "stop_rumble_dispatcher", None)
    if stop_rumble is not None:
        stop_rumble(timeout=grace_timeout)
    return False
