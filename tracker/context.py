from .models import Notification
def common(request):
    return {"unread_count": Notification.objects.filter(user=request.user, read=False).count() if request.user.is_authenticated else 0}
