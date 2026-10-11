import pytest

from minecraft_city.analysis import sample_profile
from minecraft_city.generation import generate_city
from minecraft_city.planning import blend_profiles, rule_plan


@pytest.fixture(scope="session")
def profiles():
    return [sample_profile("tokyo"), sample_profile("kyoto")]


@pytest.fixture(scope="session")
def blended(profiles):
    return blend_profiles(profiles, [0.6, 0.4])


@pytest.fixture(scope="session")
def city(blended):
    metrics, sources = blended
    return generate_city(metrics, sources, rule_plan(metrics, 42), 42)
