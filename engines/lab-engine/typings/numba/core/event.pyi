from contextlib import AbstractContextManager

class Event:
    @property
    def kind(self) -> str: ...
    @property
    def data(self) -> object: ...

class RecordingListener:
    buffer: list[tuple[float, Event]]

def install_recorder(kind: str) -> AbstractContextManager[RecordingListener]: ...
