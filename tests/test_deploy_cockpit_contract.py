"""Static contract checks for staged cockpit releases."""


def test_cockpit_deploy_stages_before_live_activation():
    script = open("scripts/deploy-cockpit-v4.sh", encoding="utf-8").read()
    assert 'RELEASES_DIR="${UI_ROOT}/releases"' in script
    assert 'rsync -a --delete "${SRC_DIR}/dist/" "$STAGE_DIR/"' in script
    assert 'ln -s "releases/${RELEASE_NAME}" "$NEXT_LINK"' in script
    assert 'mv -Tf "$NEXT_LINK" "$DST_DIR"' in script
    assert 'rsync -a --delete "${SRC_DIR}/dist/" "$DST_DIR/"' not in script
