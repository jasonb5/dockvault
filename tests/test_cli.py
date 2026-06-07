from dockvault.cli import main


def test_main_prints_name(capsys) -> None:
    main()
    captured = capsys.readouterr()
    assert captured.out == "dockvault\n"
