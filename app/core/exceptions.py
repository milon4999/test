# Custom Exceptions and Error Handling

from fastapi import HTTPException, status


class ScraperException(Exception):
    """Base exception for scraper errors"""
    def __init__(self, message: str, status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)


class UpstreamException(ScraperException):
    """Exception for upstream service errors"""
    def __init__(self, message: str = "Upstream service error"):
        super().__init__(message, status.HTTP_502_BAD_GATEWAY)


class RateLimitException(ScraperException):
    """Exception for rate limiting"""
    def __init__(self, message: str = "Rate limit exceeded"):
        super().__init__(message, status.HTTP_429_TOO_MANY_REQUESTS)


class AuthenticationException(ScraperException):
    """Exception for authentication errors"""
    def __init__(self, message: str = "Authentication failed"):
        super().__init__(message, status.HTTP_401_UNAUTHORIZED)


class AuthorizationException(ScraperException):
    """Exception for authorization errors"""
    def __init__(self, message: str = "Not authorized"):
        super().__init__(message, status.HTTP_403_FORBIDDEN)


class ValidationException(ScraperException):
    """Exception for validation errors"""
    def __init__(self, message: str = "Validation error"):
        super().__init__(message, status.HTTP_422_UNPROCESSABLE_ENTITY)


class NotFoundException(ScraperException):
    """Exception for not found errors"""
    def __init__(self, message: str = "Resource not found"):
        super().__init__(message, status.HTTP_404_NOT_FOUND)


class CacheException(Exception):
    """Exception for cache errors"""
    pass


class DatabaseException(Exception):
    """Exception for database errors"""
    pass
