#!/bin/sh
# Download a complete Jump AI checkout and run its installer from a permanent location.
set -eu

install_jumpai() {
    # Clone into a new absolute destination, forwarding arguments to the installer.
    # Fail without overwriting existing files; retain the checkout if installation fails.
    jumpai_destination=${JUMPAI_INSTALL_DIR:-"$HOME/JumpAI"}
    case "$jumpai_destination" in
        /*) ;;
        *) echo "JUMPAI_INSTALL_DIR must be an absolute path." >&2; exit 1 ;;
    esac
    if [ -e "$jumpai_destination" ] || [ -L "$jumpai_destination" ]; then
        echo "Destination already exists: $jumpai_destination. Run ./install.sh from your existing Jump AI checkout." >&2
        exit 1
    fi
    if ! command -v git >/dev/null 2>&1; then
        echo "Install Git, then rerun this command." >&2
        exit 1
    fi

    # Keep the checkout because installed skills and runtime refer to its files.
    git clone --depth 1 --branch main -- https://github.com/cashton-coleman/JumpAI.git "$jumpai_destination"
    exec sh "$jumpai_destination/install.sh" "$@"
}

# Execute only after the complete function has been received when piped from curl.
install_jumpai "$@"
