from parsing.entity_extractor import extract_project_name


def test_extract_project_name_prefers_quoted_project_title_over_body_text():
    prompt = (
        "Fuhren Sie den vollstandigen Projektzyklus fur 'Systemupgrade Grunfeld' "
        "(Grunfeld GmbH, Org.-Nr. 897772868) durch: 1) Das Projekt hat ein Budget "
        "von 492100 NOK. 2) Erfassen Sie Stunden."
    )
    assert extract_project_name(prompt) == "Systemupgrade Grunfeld"
