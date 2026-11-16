#!/usr/bin/env bash
# Build the pinned CI image, after checking that the git pin is still the base image's current
# Debian candidate. A newer git (a security update) fails here with the version to repin to,
# instead of the build silently drifting or breaking later.
#   BASE_IMAGE and GIT_VERSION come from the workflow's env; the image is tagged ddi-ci.
set -euo pipefail
: "${BASE_IMAGE:?}" "${GIT_VERSION:?}"

grep -qF "FROM $BASE_IMAGE" ci/Dockerfile \
  || { echo "::error::ci/Dockerfile is not FROM $BASE_IMAGE: keep the two pins identical"; exit 1; }

info=$(docker run --rm "$BASE_IMAGE" sh -c \
  '. /etc/os-release; apt-get update -qq >/dev/null 2>&1; echo "$VERSION_CODENAME $(apt-cache policy git | sed -n "s/^  Candidate: //p")"')
codename=${info%% *}
candidate=${info#* }
echo "base: $BASE_IMAGE ($codename); git candidate: $candidate; pinned: $GIT_VERSION"
if [ "$candidate" != "$GIT_VERSION" ]; then
  echo "::error::git pin '$GIT_VERSION' is not the $codename candidate '$candidate': set GIT_VERSION to '$candidate' in .github/workflows/ci.yml"
  exit 1
fi

docker build --pull=false --build-arg "GIT_VERSION=$GIT_VERSION" -t ddi-ci ci/
docker run --rm ddi-ci sh -c 'python --version; git --version; id -u'
