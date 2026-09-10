#!/bin/sh
# 依赖漂移提醒：比较两次修订之间变更的依赖清单，提示重新安装。
# 用法：. lib-depwatch.sh; depwatch <from> <to>
depwatch() {
    _from="$1"
    _to="$2"
    [ -z "$_from" ] && return 0
    [ -z "$_to" ] && return 0
    _pat='(^|/)(requirements[^/]*\.txt|pyproject\.toml|Pipfile|Pipfile\.lock|poetry\.lock|package\.json|package-lock\.json|pnpm-lock\.yaml|yarn\.lock|Cargo\.toml|Cargo\.lock|build\.gradle(\.kts)?|settings\.gradle(\.kts)?|gradle/libs\.versions\.toml)$'
    _changed=$(git diff --name-only "$_from" "$_to" 2>/dev/null | grep -E "$_pat")
    [ -z "$_changed" ] && return 0
    echo ""
    echo "[dep-watch] 依赖清单已变更，本地依赖可能与仓库不同步："
    echo "$_changed" | sed 's/^/           - /'
    echo ""
    echo "  建议执行："
    if printf '%s' "$_changed" | grep -q 'pnpm-lock.yaml'; then
        echo "    pnpm install"
    fi
    if printf '%s' "$_changed" | grep -q 'yarn.lock'; then
        echo "    yarn install"
    fi
    if printf '%s' "$_changed" | grep -q 'package-lock.json'; then
        echo "    npm install"
    fi
    if printf '%s' "$_changed" | grep -qE '(^|/)package\.json$'; then
        if ! printf '%s' "$_changed" | grep -qE 'pnpm-lock\.yaml|yarn\.lock|package-lock\.json'; then
            echo "    pnpm install   (或 npm install / yarn install)"
        fi
    fi
    for _rf in $(printf '%s\n' "$_changed" | grep -E '(^|/)requirements[^/]*\.txt$'); do
        echo "    pip install -r $_rf"
    done
    if printf '%s' "$_changed" | grep -qE '(^|/)pyproject\.toml$'; then
        echo "    pip install -e .   (poetry 项目用: poetry install)"
    fi
    if printf '%s' "$_changed" | grep -qE '(^|/)Cargo\.(toml|lock)$'; then
        echo "    cargo fetch"
    fi
    if printf '%s' "$_changed" | grep -qE 'gradle'; then
        echo "    ./gradlew --refresh-dependencies   (Android Studio: Sync Project with Gradle Files)"
    fi
    echo ""
}
