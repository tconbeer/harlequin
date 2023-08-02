from pathlib import Path
from typing import Union

from textual.suggester import Suggester


class PathSuggester(Suggester):
    def __init__(self) -> None:
        super().__init__(use_cache=True, case_sensitive=True)

    async def get_suggestion(self, value: str) -> Union[str, None]:
        try:
            p = Path(value).expanduser()
            matches = list(p.parent.glob(f"{p.parts[-1]}*"))
            if len(matches) == 1:
                return str(matches[0])
            else:
                return None
        except Exception:
            return None
