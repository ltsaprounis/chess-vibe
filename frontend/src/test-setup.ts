import '@testing-library/jest-dom'

// jsdom does not implement scrollIntoView — stub it globally for all tests.
Element.prototype.scrollIntoView = () => {}
